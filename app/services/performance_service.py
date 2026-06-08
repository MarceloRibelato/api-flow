import time
import uuid
import logging
import concurrent.futures
from datetime import datetime, timezone
import requests
import urllib3
import queue
import csv
import os
from sqlalchemy.orm import Session
from app.models.performance_models import PerformanceTestResult

# Suppress insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Global dict to store active load test states for stopping
active_tests = {}

class PerformanceService:
    @staticmethod
    def run_load_test_sync(
        db: Session,
        job_id: str,
        api_list: list, 
        virtual_users: int, 
        duration_seconds: int, 
        ramp_up_seconds: int,
        company_id: int, 
        user_id: int,
        test_name: str = None
    ):
        """
        Executes a load test for a list of API endpoints sequentially per virtual user.
        """
        if not api_list:
            logger.error("No APIs provided for load test.")
            return

        result = db.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id).first()
        if not result:
            target_url = api_list[0].get("url", "unknown_url") if len(api_list) == 1 else "Multiple APIs (Flow)"
            result = PerformanceTestResult(
                id=job_id,
                company_id=company_id,
                user_id=user_id,
                status="running",
                virtual_users=virtual_users,
                duration_seconds=duration_seconds,
                ramp_up_seconds=ramp_up_seconds,
                test_name=test_name,
                target_url=target_url
            )
            db.add(result)
        else:
            result.status = "running"
        db.add(result)
        db.commit()
        db.refresh(result)

        # Pre-process headers and formats
        import json
        from app.services.variable_resolver import replace_vars
        from app.services.extraction_engine import process_extractions
        
        import threading
        api_cache = {}
        api_cache_lock = threading.Lock()

        parsed_apis = []
        for api in api_list:
            method = api.get("method", "GET").upper()
            url_template = api.get("url", "")
            headers_raw = api.get("headers", [])
            headers = {}
            if isinstance(headers_raw, list):
                for h in headers_raw:
                    if isinstance(h, dict) and h.get("key"):
                        headers[h["key"]] = h.get("value", "")
            elif isinstance(headers_raw, dict):
                headers = headers_raw
                
            headers_lower = {k.lower(): v for k, v in headers.items()}
            if "user-agent" not in headers_lower:
                headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            if "accept" not in headers_lower:
                headers["Accept"] = "*/*"

            body = api.get("body", "")
            if isinstance(body, dict):
                body = json.dumps(body)
                
            parsed_apis.append({
                "id": api.get("id", f"api_{len(parsed_apis)}"),
                "name": api.get("name", "Unknown API"),
                "method": method,
                "url": url_template,
                "headers": headers,
                "body": body,
                "extractions": api.get("variableDefinitions", []) or api.get("extracts", []),
                "cache_ttl_minutes": int(api.get("cacheTTL", 0) or 0)
            })

        stop_time = time.time() + duration_seconds
        active_tests[job_id] = True
        
        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "latencies": []
        }
        
        stats_per_api = {
            api["id"]: {
                "total": 0,
                "success": 0,
                "failed": 0,
                "latencies": [],
                "name": api.get("name", ""),
                "url": api.get("url", ""),
                "method": api.get("method", "GET")
            } for api in parsed_apis
        }
        
        # We'll collect coarse time series 
        time_series = []
        
        # Setup Detailed CSV Logger
        log_queue = queue.Queue()
        csv_file_path = f"/tmp/{job_id}_detailed.csv"
        
        def csv_writer_worker():
            try:
                os.makedirs("/tmp", exist_ok=True)
                with open(csv_file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "API ID", "Method", "URL", "Status Code", "Latency (ms)", "Error"])
                    while True:
                        try:
                            item = log_queue.get(timeout=1.0)
                            if item is None: # Sentinel value to stop
                                break
                            writer.writerow(item)
                            log_queue.task_done()
                        except queue.Empty:
                            if not active_tests.get(job_id, False):
                                break
            except Exception as e:
                logger.error(f"Error in CSV writer thread: {e}")

        # Start CSV writer thread
        writer_thread = threading.Thread(target=csv_writer_worker)
        writer_thread.daemon = True
        writer_thread.start()

        def worker(initial_delay: float):
            if initial_delay > 0:
                time.sleep(initial_delay)
            # Use Session to pool connections
            with requests.Session() as session:
                session.trust_env = False
                vu_variables = {}
                while time.time() < stop_time and active_tests.get(job_id, False):
                    # Iterate sequentially over the flow
                    for api in parsed_apis:
                        if time.time() >= stop_time or not active_tests.get(job_id, False):
                            break
                            
                        # --- CACHE CHECK ---
                        use_cache = False
                        cache_key = api["id"]
                        extracted_delta = {}
                        
                        if api["cache_ttl_minutes"] > 0:
                            with api_cache_lock:
                                entry = api_cache.get(cache_key)
                                if entry and (time.time() - entry["timestamp"]) <= (api["cache_ttl_minutes"] * 60):
                                    use_cache = True
                                    extracted_delta = entry["extracted_vars"]
                        
                        if use_cache:
                            # Apply cached variables
                            vu_variables.update(extracted_delta)
                            # Do not record latency or count for cached hit. 
                            # The user explicitly said: "pois o teste dela não sera considerado"
                            # This means we should skip it entirely in the metrics.
                            continue # Skip the real HTTP request and metric counting

                        start_t = time.time()
                        
                        # Resolve variables for this step
                        current_url = replace_vars(api["url"], vu_variables)
                        current_body = replace_vars(api["body"], vu_variables) if api["body"] else ""
                        current_headers = {k: replace_vars(v, vu_variables) for k, v in api["headers"].items()}
                        
                        try:
                            resp = session.request(
                                api["method"], 
                                current_url, 
                                headers=current_headers, 
                                data=current_body, 
                                timeout=10, 
                                verify=False
                            )
                            latency = (time.time() - start_t) * 1000
                            stats["total"] += 1
                            stats["latencies"].append(latency)
                            stats_per_api[api["id"]]["total"] += 1
                            stats_per_api[api["id"]]["latencies"].append(latency)
                            
                            if resp.status_code < 400:
                                stats["success"] += 1
                                stats_per_api[api["id"]]["success"] += 1
                                log_queue.put([
                                    datetime.now().isoformat(),
                                    api["id"],
                                    api["method"],
                                    current_url,
                                    resp.status_code,
                                    round(latency, 2),
                                    ""
                                ])
                                # Extract variables if configured
                                if api["extractions"]:
                                    resp_json = None
                                    try:
                                        resp_json = resp.json()
                                    except:
                                        pass
                                    old_vars = vu_variables.copy()
                                    process_extractions(api["extractions"], resp.headers, resp_json, vu_variables)
                                    
                                    # Calculate what was extracted
                                    step_delta = {k: vu_variables[k] for k in vu_variables if k not in old_vars or vu_variables[k] != old_vars[k]}
                                    
                                    # Save to cache if enabled
                                    if api["cache_ttl_minutes"] > 0:
                                        with api_cache_lock:
                                            api_cache[cache_key] = {
                                                "timestamp": time.time(),
                                                "extracted_vars": step_delta
                                            }
                                else:
                                    if api["cache_ttl_minutes"] > 0:
                                        with api_cache_lock:
                                            api_cache[cache_key] = {
                                                "timestamp": time.time(),
                                                "extracted_vars": {}
                                            }
                            else:
                                stats["failed"] += 1
                                stats_per_api[api["id"]]["failed"] += 1
                                log_queue.put([
                                    datetime.now().isoformat(),
                                    api["id"],
                                    api["method"],
                                    current_url,
                                    resp.status_code,
                                    round(latency, 2),
                                    resp.text[:100]
                                ])
                                if stats["failed"] <= 5: # Log first 5 failures
                                    logger.error(f"Load test failed with status {resp.status_code}: {resp.text[:200]}")
                        except Exception as e:
                            latency = (time.time() - start_t) * 1000
                            stats["total"] += 1
                            stats["failed"] += 1
                            stats["latencies"].append(latency)
                            stats_per_api[api["id"]]["total"] += 1
                            stats_per_api[api["id"]]["failed"] += 1
                            stats_per_api[api["id"]]["latencies"].append(latency)
                            log_queue.put([
                                datetime.now().isoformat(),
                                api["id"],
                                api["method"],
                                current_url,
                                0,
                                round(latency, 2),
                                str(e)
                            ])
                            if stats["failed"] <= 5:
                                logger.error(f"Load test request exception: {e}")
                            # Sleep briefly on error to prevent CPU spin
                            time.sleep(0.1)

        logger.info(f"Starting Load Test {job_id} with {virtual_users} VUs for {duration_seconds}s")
        start_exec_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=virtual_users) as executor:
            futures = []
            for i in range(virtual_users):
                if virtual_users > 1 and ramp_up_seconds > 0:
                    delay = (i / (virtual_users - 1)) * ramp_up_seconds
                else:
                    delay = 0
                futures.append(executor.submit(worker, delay))
            
            # Monitor loop
            last_total = 0
            last_success = 0
            last_failed = 0
            last_latencies_len = 0
            
            last_stats_per_api = {
                api_id: {"total": 0, "latencies_len": 0} for api_id in stats_per_api
            }
            
            while time.time() < stop_time and active_tests.get(job_id, False):
                # Check if test was stopped from the API (status updated in DB)
                try:
                    db.refresh(result)
                    if result.status == "stopped":
                        active_tests[job_id] = False
                        break
                except:
                    pass
                
                time.sleep(1)
                curr_total = stats["total"]
                curr_success = stats["success"]
                curr_failed = stats["failed"]
                curr_latencies_len = len(stats["latencies"])

                rps = curr_total - last_total
                success_ps = curr_success - last_success
                errors_ps = curr_failed - last_failed
                
                recent_latencies = stats["latencies"][last_latencies_len:curr_latencies_len]
                avg_latency = sum(recent_latencies) / len(recent_latencies) if recent_latencies else 0

                last_total = curr_total
                last_success = curr_success
                last_failed = curr_failed
                last_latencies_len = curr_latencies_len
                
                elapsed = time.time() - start_exec_time
                if ramp_up_seconds > 0:
                    if elapsed >= ramp_up_seconds:
                        active_users = virtual_users
                    else:
                        active_users = min(virtual_users, int((elapsed / ramp_up_seconds) * (virtual_users - 1)) + 1)
                else:
                    active_users = virtual_users

                api_metrics = {}
                for api_id, s in stats_per_api.items():
                    curr_api_total = s["total"]
                    curr_api_latencies_len = len(s["latencies"])
                    
                    api_rps = curr_api_total - last_stats_per_api[api_id]["total"]
                    
                    recent_api_latencies = s["latencies"][last_stats_per_api[api_id]["latencies_len"]:curr_api_latencies_len]
                    api_avg_latency = sum(recent_api_latencies) / len(recent_api_latencies) if recent_api_latencies else 0
                    
                    api_metrics[api_id] = {
                        "rps": api_rps,
                        "avg_latency": api_avg_latency,
                        "name": s["name"],
                        "url": s["url"],
                        "method": s["method"]
                    }
                    
                    last_stats_per_api[api_id]["total"] = curr_api_total
                    last_stats_per_api[api_id]["latencies_len"] = curr_api_latencies_len

                time_series.append({
                    "timestamp": int(time.time() * 1000),
                    "rps": rps,
                    "success_ps": success_ps,
                    "errors_ps": errors_ps,
                    "errors": stats["failed"],
                    "success": stats["success"],
                    "active_users": active_users,
                    "avg_latency": avg_latency,
                    "apis": api_metrics
                })
                
                # Update DB for live tracking
                result.time_series_data = time_series
                result.total_requests = stats["total"]
                result.success_requests = stats["success"]
                result.failed_requests = stats["failed"]
                try:
                    db.commit()
                except Exception as e:
                    logger.error(f"Live update failed: {e}")
            
            # Wait for threads to finish their last requests
            concurrent.futures.wait(futures)

        # Stop the CSV writer thread
        log_queue.put(None)
        writer_thread.join()

        actual_duration = time.time() - start_exec_time

        # Compute final stats
        def percentile(data, p):
            if not data: return 0.0
            if len(data) == 1: return data[0]
            k = (len(data) - 1) * p
            f = int(k)
            c = f + 1
            if f == c: return data[int(k)]
            d0 = data[f] * (c - k)
            d1 = data[c] * (k - f)
            return d0 + d1

        latencies = stats["latencies"]
        if latencies:
            latencies.sort()
            result.avg_latency = sum(latencies) / len(latencies)
            result.min_latency = latencies[0]
            result.max_latency = latencies[-1]
            result.p50_latency = percentile(latencies, 0.50)
            result.p90_latency = percentile(latencies, 0.90)
            result.p95_latency = percentile(latencies, 0.95)
            result.p99_latency = percentile(latencies, 0.99)
            
        final_api_stats = {}
        for api_id, s in stats_per_api.items():
            api_lats = s["latencies"]
            api_lats.sort()
            final_api_stats[api_id] = {
                "total_requests": s["total"],
                "success_requests": s["success"],
                "failed_requests": s["failed"],
                "avg_latency": sum(api_lats) / len(api_lats) if api_lats else 0.0,
                "min_latency": api_lats[0] if api_lats else 0.0,
                "max_latency": api_lats[-1] if api_lats else 0.0,
                "p50_latency": percentile(api_lats, 0.50),
                "p90_latency": percentile(api_lats, 0.90),
                "p95_latency": percentile(api_lats, 0.95),
                "p99_latency": percentile(api_lats, 0.99),
                "requests_per_second": (s["total"] / actual_duration) if actual_duration > 0 else 0.0,
                "name": s["name"],
                "url": s["url"],
                "method": s["method"]
            }
            
        result.api_stats = final_api_stats
        
        result.total_requests = stats["total"]
        result.success_requests = stats["success"]
        result.failed_requests = stats["failed"]
        if actual_duration > 0:
            result.requests_per_second = stats["total"] / actual_duration
        
        result.time_series_data = time_series
        if result.status != "stopped":
            result.status = "completed"
        result.completed_at = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(result)

        # Clean up global state
        if job_id in active_tests:
            del active_tests[job_id]

        logger.info(f"Load Test {job_id} finished. RPS: {result.requests_per_second:.2f}")
        return result

    @staticmethod
    def stop_load_test(job_id: str):
        from app.database import SessionLocal
        from app.models.performance_models import PerformanceTestResult
        
        # 1. Fallback for local thread (if not using Celery)
        if job_id in active_tests:
            active_tests[job_id] = False
            
        # 2. Global state for Celery Worker (update DB)
        db = SessionLocal()
        try:
            result = db.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id).first()
            if result and result.status == "running":
                result.status = "stopped"
                db.commit()
                return True
            return False
        finally:
            db.close()

    @staticmethod
    def delete_test_result(db: Session, job_id: str, company_id: int):
        result = db.query(PerformanceTestResult).filter(
            PerformanceTestResult.id == job_id,
            PerformanceTestResult.company_id == company_id
        ).first()
        if not result:
            return False
        db.delete(result)
        db.commit()
        return True

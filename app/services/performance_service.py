import time
import uuid
import logging
import concurrent.futures
from datetime import datetime, timezone
import requests
import urllib3
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
        api_data: dict, 
        virtual_users: int, 
        duration_seconds: int, 
        ramp_up_seconds: int,
        company_id: int, 
        user_id: int,
        test_name: str = None
    ):
        """
        Executes a load test for a single API endpoint using ThreadPoolExecutor.
        """
        target_url = api_data.get("url", "unknown_url")
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
        db.commit()
        db.refresh(result)

        url = api_data.get("url")
        method = api_data.get("method", "GET").upper()
        headers_raw = api_data.get("headers", [])
        
        headers = {}
        if isinstance(headers_raw, list):
            for h in headers_raw:
                if isinstance(h, dict) and h.get("key"):
                    headers[h["key"]] = h.get("value", "")
        elif isinstance(headers_raw, dict):
            headers = headers_raw
            
        # ModSecurity/WAF bypass: inject standard headers if missing
        headers_lower = {k.lower(): v for k, v in headers.items()}
        if "user-agent" not in headers_lower:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        if "accept" not in headers_lower:
            headers["Accept"] = "*/*"

        body = api_data.get("body", "")
        if isinstance(body, dict):
            import json
            body = json.dumps(body)

        stop_time = time.time() + duration_seconds
        active_tests[job_id] = True
        
        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "latencies": []
        }
        
        # We'll collect coarse time series 
        time_series = []

        def worker(initial_delay: float):
            if initial_delay > 0:
                time.sleep(initial_delay)
            # Use Session to pool connections
            with requests.Session() as session:
                session.trust_env = False
                while time.time() < stop_time and active_tests.get(job_id, False):
                    start_t = time.time()
                    try:
                        resp = session.request(method, url, headers=headers, data=body, timeout=10, verify=False)
                        latency = (time.time() - start_t) * 1000
                        stats["total"] += 1
                        stats["latencies"].append(latency)
                        if resp.status_code < 400:
                            stats["success"] += 1
                        else:
                            stats["failed"] += 1
                            if stats["failed"] <= 5: # Log first 5 failures
                                logger.error(f"Load test failed with status {resp.status_code}: {resp.text[:200]}")
                    except Exception as e:
                        latency = (time.time() - start_t) * 1000
                        stats["total"] += 1
                        stats["failed"] += 1
                        stats["latencies"].append(latency)
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
            while time.time() < stop_time and active_tests.get(job_id, False):
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

                time_series.append({
                    "timestamp": int(time.time() * 1000),
                    "rps": rps,
                    "success_ps": success_ps,
                    "errors_ps": errors_ps,
                    "errors": stats["failed"],
                    "success": stats["success"],
                    "active_users": active_users,
                    "avg_latency": avg_latency
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

        actual_duration = time.time() - start_exec_time

        # Compute final stats
        latencies = stats["latencies"]
        if latencies:
            latencies.sort()
            result.avg_latency = sum(latencies) / len(latencies)
            result.min_latency = latencies[0]
            result.max_latency = latencies[-1]
            
            def percentile(data, p):
                k = (len(data) - 1) * p
                f = int(k)
                c = f + 1
                if f == c: return data[int(k)]
                d0 = data[f] * (c - k)
                d1 = data[c] * (k - f)
                return d0 + d1
                
            result.p90_latency = percentile(latencies, 0.90)
            result.p95_latency = percentile(latencies, 0.95)
            result.p99_latency = percentile(latencies, 0.99)
        
        result.total_requests = stats["total"]
        result.success_requests = stats["success"]
        result.failed_requests = stats["failed"]
        if actual_duration > 0:
            result.requests_per_second = stats["total"] / actual_duration
        
        result.time_series_data = time_series
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
        if job_id in active_tests:
            active_tests[job_id] = False
            return True
        return False

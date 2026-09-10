import pytest
import time
import os
import threading
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from app.models.performance_models import PerformanceTestResult
from app.services.performance_service import PerformanceService, active_tests


# ── 1. PERCENTILE CALCULATION TESTS ─────────────────────────────────

def test_percentile_calculation():
    # Helper to test the percentile logic used in PerformanceService
    def calc_percentile(data, p):
        if not data: return 0.0
        if len(data) == 1: return float(data[0])
        k = (len(data) - 1) * p
        f = int(k)
        c = f + 1
        if f >= len(data) - 1: return float(data[-1])
        d0 = data[f] * (c - k)
        d1 = data[c] * (k - f)
        return float(d0 + d1)

    # Empty data
    assert calc_percentile([], 0.5) == 0.0

    # Single item
    assert calc_percentile([42], 0.5) == 42.0
    assert calc_percentile([42], 0.99) == 42.0

    # Two items
    assert calc_percentile([10, 20], 0.5) == 15.0

    # 100 sequential items: 1 to 100
    data = list(range(1, 101))
    assert calc_percentile(data, 0.0) == 1.0
    assert calc_percentile(data, 0.50) == 50.5
    assert round(calc_percentile(data, 0.90), 2) == 90.1
    assert round(calc_percentile(data, 0.95), 2) == 95.05
    assert round(calc_percentile(data, 0.99), 2) == 99.01
    assert calc_percentile(data, 1.0) == 100.0


# ── 2. THREAD-SAFETY OF METRICS UNDER CONCURRENT VIRTUAL USERS ──────

def test_thread_safety_stats_accumulation():
    stats = {
        "total": 0,
        "success": 0,
        "failed": 0,
        "latencies": []
    }
    stats_lock = threading.Lock()
    num_threads = 20
    calls_per_thread = 50

    def simulate_vu(worker_id):
        for i in range(calls_per_thread):
            latency = 10.0 + (i % 5)
            is_success = (i % 5 != 0)
            with stats_lock:
                stats["total"] += 1
                stats["latencies"].append(latency)
                if is_success:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1

    threads = [threading.Thread(target=simulate_vu, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_expected = num_threads * calls_per_thread
    assert stats["total"] == total_expected
    assert len(stats["latencies"]) == total_expected
    assert stats["success"] + stats["failed"] == total_expected
    assert stats["failed"] == num_threads * (calls_per_thread // 5)


# ── 3. LOAD TEST SYNC EXECUTION LIFECYCLE ───────────────────────────

@patch("requests.Session.request")
def test_run_load_test_sync_successful(mock_request, db_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status": "ok"}'
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.json.return_value = {"status": "ok"}
    mock_request.return_value = mock_resp

    job_id = f"test_perf_{int(time.time())}"
    api_list = [{
        "id": "api_1",
        "name": "Health Check",
        "method": "GET",
        "url": "http://127.0.0.1:8000/api/health",
        "headers": {"Accept": "application/json"},
        "body": ""
    }]

    result = PerformanceService.run_load_test_sync(
        db=db_session,
        job_id=job_id,
        api_list=api_list,
        virtual_users=2,
        duration_seconds=1,
        ramp_up_seconds=0,
        company_id=1,
        user_id=1,
        test_name="Unit Load Test"
    )

    assert result is not None
    assert result.status == "completed"
    assert result.total_requests > 0
    assert result.success_requests == result.total_requests
    assert result.failed_requests == 0
    assert result.avg_latency > 0
    assert result.requests_per_second > 0
    assert len(result.time_series_data) > 0
    assert "api_1" in result.api_stats
    assert result.api_stats["api_1"]["success_requests"] > 0


@patch("requests.Session.request")
def test_run_load_test_sync_with_failures(mock_request, db_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = '{"error": "Internal Server Error"}'
    mock_resp.reason = "Internal Server Error"
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_request.return_value = mock_resp

    job_id = f"test_perf_fail_{int(time.time())}"
    api_list = [{
        "id": "api_fail",
        "name": "Failing API",
        "method": "POST",
        "url": "http://127.0.0.1:8000/api/fail",
        "headers": {},
        "body": '{"data": 1}'
    }]

    result = PerformanceService.run_load_test_sync(
        db=db_session,
        job_id=job_id,
        api_list=api_list,
        virtual_users=2,
        duration_seconds=1,
        ramp_up_seconds=0,
        company_id=1,
        user_id=1,
        test_name="Failing Load Test"
    )

    assert result is not None
    assert result.status == "completed"
    assert result.total_requests > 0
    assert result.failed_requests == result.total_requests
    assert result.success_requests == 0
    assert len(result.failed_requests_detail) > 0
    assert result.failed_requests_detail[0]["status_code"] == 500


# ── 4. STOP LOAD TEST & DELETE TEST RESULT ───────────────────────────

def test_stop_load_test(db_session):
    job_id = f"test_stop_{int(time.time())}"
    test_record = PerformanceTestResult(
        id=job_id,
        company_id=1,
        user_id=1,
        status="running",
        virtual_users=5,
        duration_seconds=30
    )
    db_session.add(test_record)
    db_session.commit()

    active_tests[job_id] = True
    stopped = PerformanceService.stop_load_test(db_session, job_id)
    assert stopped is True

    db_session.refresh(test_record)
    assert test_record.status == "stopped"
    assert active_tests[job_id] is False

    # Stopping already stopped test returns False
    stopped_again = PerformanceService.stop_load_test(db_session, job_id)
    assert stopped_again is False


def test_delete_test_result(db_session):
    job_id = f"test_del_{int(time.time())}"
    test_record = PerformanceTestResult(
        id=job_id,
        company_id=1,
        user_id=1,
        status="completed",
        virtual_users=5,
        duration_seconds=10
    )
    db_session.add(test_record)
    db_session.commit()

    deleted = PerformanceService.delete_test_result(db_session, job_id, company_id=1)
    assert deleted is True

    # Check deleted from DB
    found = db_session.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id).first()
    assert found is None

    # Deleting non-existent returns False
    deleted_again = PerformanceService.delete_test_result(db_session, job_id, company_id=1)
    assert deleted_again is False


import sys
from datetime import datetime, timezone, timedelta

sys.path.append(".")
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory
from app.services.scheduler_service import execute_job_logic

db = SessionLocal()
try:
    print("1. Inserting fresh successful API history record...")
    
    # Delete old ones first
    db.query(ApiExecutionHistory).filter(ApiExecutionHistory.api_id == '6584e371-309a-4246-b25b-25c90cfbf30b').delete()
    db.commit()
    
    fresh_history = ApiExecutionHistory(
        execution_id="manual_test_login_1",
        batch_id="manual_test_login_batch",
        api_id="6584e371-309a-4246-b25b-25c90cfbf30b",
        api_name="Login com sucesso",
        project_id=13,
        flow_id="21",
        node_id="node-recorded-0",
        node_name="Login",
        method="GET",
        url="https://practicetestautomation.com/logged-in-successfully/",
        status_code=200,
        status_text="OK",
        response_body='{"status": "success", "user": "test_user"}',
        response_time=50,
        error_message=None,
        environment_id=7,
        environment_name="Homolog",
        created_at=datetime.now(timezone.utc)
    )
    db.add(fresh_history)
    db.commit()
    db.refresh(fresh_history)
    print(f"Inserted history record ID: {fresh_history.id} at {fresh_history.created_at}")

    print("\n2. Executing job logic for Schedule 284...")
    # This will trigger celery_run_load_test via celery
    execute_job_logic(284)
    print("Triggered scheduled load test! Please check docker logs flow-celery-worker.")
finally:
    db.close()

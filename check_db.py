from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory

db = SessionLocal()
histories = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).limit(15).all()

for h in histories:
    print(f"ID: {h.id}, Origin: {h.trigger_origin}, Schedule ID: {h.schedule_id}, Batch ID: {h.batch_id}, Project ID: {h.project_id}, API Name: {h.api_name}, Exec Type: {h.execution_type}")

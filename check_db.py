import sys
sys.path.append("/home/server/Desktop/Projetos/Flow/api-flow")
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory

db = SessionLocal()

# Find the most recent batch_id
latest_batch = db.query(ApiExecutionHistory.batch_id).filter(ApiExecutionHistory.batch_id.isnot(None)).order_by(desc(ApiExecutionHistory.created_at)).first()

if not latest_batch:
    print("No history found")
    sys.exit(0)

batch_id = latest_batch[0]
print(f"Latest batch_id: {batch_id}")

calls = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.batch_id == batch_id).all()
print(f"Number of calls with this batch_id: {len(calls)}")
for c in calls:
    print(f" - id: {c.id}, node_id: {c.node_id}, api_name: {c.api_name}")


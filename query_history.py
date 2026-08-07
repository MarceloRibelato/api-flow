import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory
import json

db = SessionLocal()
histories = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).limit(10).all()

for h in histories:
    print(f"ID: {h.id} | Name: {h.api_name} | Success: {h.success} | Batch: {h.batch_id}")
    try:
        assertions = h.assertions if isinstance(h.assertions, list) else json.loads(h.assertions)
        for a in assertions:
            print(f"  - {a}")
    except:
        pass
    print("-" * 50)

import json
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory

db = SessionLocal()
histories = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).limit(5).all()

for h in histories:
    print(f"ID: {h.id}")
    print(f"Method: {h.method}, URL: {h.url}")
    print(f"Error Message: {repr(h.error_message)}")
    print(f"Success Property: {h.success}")
    if h.assertions:
        # Print just a summary of assertions
        passed_count = sum(1 for a in h.assertions if a.get('success', False) or a.get('passed', False))
        print(f"Assertions: {passed_count}/{len(h.assertions)} passed")
    else:
        print("Assertions: None")
    print("-" * 40)

db.close()

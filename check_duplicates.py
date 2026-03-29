
from app.services.history_service import HistoryService
from app.database import SessionLocal
from sqlalchemy import text
import os

db = SessionLocal()

# Find a batch_id
batch_res = db.execute(text("SELECT batch_id FROM api_test_execution_history WHERE batch_id IS NOT NULL LIMIT 1")).fetchone()

if batch_res:
    batch_id = batch_res[0]
    print(f"Checking HistoryService.get_all for batch_id: {batch_id}")
    
    # We need a company_id. Assuming 1.
    res = HistoryService.get_all(db, company_id=1, batch_id=batch_id, limit=1000)
    
    print(f"Total items returned: {res['total']}")
    print(f"Items in list: {len(res['items'])}")
    
    # Check for duplicate execution_ids in the result list
    ids = [item.execution_id for item in res['items']]
    unique_ids = set(ids)
    
    if len(ids) != len(unique_ids):
        print(f"!!! DUPLICATES DETECTED: {len(ids)} items total, but only {len(unique_ids)} unique execution_ids.")
        # Find which ones
        seen = {}
        for x in ids:
            seen[x] = seen.get(x, 0) + 1
        
        for x, count in seen.items():
            if count > 1:
                print(f"ID {x} appears {count} times")
    else:
        print("No duplicate execution_ids in the result list.")

db.close()

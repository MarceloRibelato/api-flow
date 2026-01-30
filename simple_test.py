"""
Simple test of flow load
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.services.flow_service import FlowService

def test():
    db = SessionLocal()
    try:
        print("Testing flow load...")
        result = FlowService.load(db, project_id=1, company_id=1)
        
        print(f"ID: {result.get('id')}")
        print(f"Name: {result.get('name')}")
        
        if result.get('id'):
            print("SUCCESS")
        else:
            print("FAILED")
        
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    test()

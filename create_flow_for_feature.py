"""
Create a flow for the existing feature
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.services.flow_service import FlowService

def create_flow():
    db = SessionLocal()
    try:
        # Create flow for feature ID 1 (Login)
        print("Creating flow for feature ID 1...")
        flow = FlowService.create(db, project_id=1, name="Login Flow", company_id=1)
        
        if flow:
            print(f"SUCCESS! Flow created:")
            print(f"  - ID: {flow.id}")
            print(f"  - Name: {flow.name}")
            print(f"  - Project ID: {flow.project_id}")
        else:
            print("ERROR: Failed to create flow (ownership verification failed)")
            
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    create_flow()

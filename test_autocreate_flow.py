"""
Test flow load with auto-creation for a new feature
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.services.flow_service import FlowService

def test_load_with_autocreate():
    db = SessionLocal()
    try:
        # First, delete the existing flow to test auto-creation
        from app.models.flow_models import FlowDB
        db.query(FlowDB).filter(FlowDB.project_id == 1).delete()
        db.commit()
        print("Deleted existing flow for project_id=1")
        
        # Now try to load - should auto-create
        print("\nLoading flow for project_id=1 (should auto-create)...")
        result = FlowService.load(db, project_id=1, company_id=1)
        
        print(f"\nRESULT:")
        print(f"  - ID: {result.get('id')}")
        print(f"  - Name: {result.get('name')}")
        print(f"  - Nodes: {len(result.get('nodes', []))}")
        print(f"  - Edges: {len(result.get('edges', []))}")
        print(f"  - Cards: {len(result.get('cardData', {}))}")
        
        if result.get('id'):
            print("\n✅ SUCCESS! Flow was auto-created")
        else:
            print("\n❌ FAILED! Flow was not created")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_load_with_autocreate()

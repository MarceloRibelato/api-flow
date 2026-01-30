"""
Test flow load to see the actual error
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.services.flow_service import FlowService

def test_load_flow():
    db = SessionLocal()
    try:
        print("Loading flow for project_id=1, company_id=1...")
        result = FlowService.load(db, project_id=1, company_id=1)
        print(f"SUCCESS! Flow loaded:")
        print(f"  - ID: {result.get('id')}")
        print(f"  - Name: {result.get('name')}")
        print(f"  - Nodes: {len(result.get('nodes', []))}")
        print(f"  - Edges: {len(result.get('edges', []))}")
        print(f"  - Cards: {len(result.get('cardData', {}))}")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        print(f"ERROR TYPE: {type(e).__name__}")
        import traceback
        for line in traceback.format_exc().split('\n'):
            print(line)
    finally:
        db.close()

if __name__ == "__main__":
    test_load_flow()

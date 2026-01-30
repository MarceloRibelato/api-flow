"""
Check database state
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from app.models.user_models import UserDB

def check_db():
    db = SessionLocal()
    try:
        users = db.query(UserDB).all()
        print(f"Users: {len(users)}")
        for u in users:
            print(f"  - ID: {u.id}, Username: {u.username}, Company ID: {u.company_id}")
        
        products = db.query(ProductModel).all()
        print(f"\nProducts: {len(products)}")
        for p in products:
            print(f"  - ID: {p.id}, Name: {p.name}, Company ID: {p.company_id}")
        
        features = db.query(FeatureModel).all()
        print(f"\nFeatures: {len(features)}")
        for f in features:
            print(f"  - ID: {f.id}, Name: {f.name}, Product ID: {f.product_id}")
        
        flows = db.query(FlowDB).all()
        print(f"\nFlows: {len(flows)}")
        for flow in flows:
            print(f"  - ID: {flow.id}, Name: {flow.name}, Project ID: {flow.project_id}")
            
    finally:
        db.close()

if __name__ == "__main__":
    check_db()

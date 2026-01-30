"""
Debug ownership verification
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models.feature_models import FeatureModel
from app.models.product_models import ProductModel

def debug_ownership():
    db = SessionLocal()
    try:
        # Check feature
        feature = db.query(FeatureModel).filter(FeatureModel.id == 1).first()
        if feature:
            print(f"Feature ID: {feature.id}, Name: {feature.name}, Product ID: {feature.product_id}")
            
            # Check product
            product = db.query(ProductModel).filter(ProductModel.id == feature.product_id).first()
            if product:
                print(f"Product ID: {product.id}, Name: {product.name}, Company ID: {product.company_id}")
            else:
                print("Product not found!")
        else:
            print("Feature not found!")
            
        # Try the join query
        result = db.query(FeatureModel).join(ProductModel).filter(
            FeatureModel.id == 1,
            ProductModel.company_id == 1
        ).first()
        
        if result:
            print("Ownership verification: PASSED")
        else:
            print("Ownership verification: FAILED")
            
    finally:
        db.close()

if __name__ == "__main__":
    debug_ownership()

"""
Script to seed initial data after database reset
"""
import sys
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.schemas.auth_schemas import UserCreate
from app.services.auth_service import AuthService
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.services.flow_service import FlowService

def seed_data():
    db = SessionLocal()
    try:
        print("🌱 Seeding database...")
        
        # 1. Create User
        print("Creating user...")
        user_data = UserCreate(
            username="admin@flow.com", # Changed to ensure new unique data
            email="admin@flow.com",
            full_name="Admin User",
            cpf="111.111.111-11",
            company="Flow Corp",
            cnpj="11.111.111/1111-11", 
            phone="(11) 99999-9999",
            password="admin"
        )
        
        try:
           user = AuthService.create_user(db, user_data)
           print(f"✅ User created! ID: {user.id} (admin@flow.com / admin)")
        except Exception as e:
           print(f"User creation skipped (might exist): {e}")
           from app.models.user_models import UserDB
           user = db.query(UserDB).filter(UserDB.username == "admin@flow.com").first()

        
        # 2. Create Product
        print("\nCreating Product...")
        existing_product = db.query(ProductModel).filter(ProductModel.name == "Flow App").first()
        if not existing_product:
            product = ProductModel(name="Flow App", company_id=user.company_id)
            db.add(product)
            db.commit()
            db.refresh(product)
            print(f"✅ Product created! ID: {product.id}")
        else:
            product = existing_product
            print(f"Product already exists. ID: {product.id}")
            
        # 3. Create Feature
        print("\nCreating Feature...")
        existing_feature = db.query(FeatureModel).filter(FeatureModel.name == "Login Feature").first()
        if not existing_feature:
            feature = FeatureModel(name="Login Feature", product_id=product.id)
            db.add(feature)
            db.commit()
            db.refresh(feature)
            print(f"✅ Feature created! ID: {feature.id}")
        else:
            feature = existing_feature
            print(f"Feature already exists. ID: {feature.id}")

        # 4. Check/Create Flow
        print("\nChecking Flow...")
        flow = FlowService.load(db, project_id=feature.id, company_id=user.company_id)
        if flow.get('id'):
             print(f"✅ Flow ready! ID: {flow.get('id')}")
        else:
             print("❌ Flow check failed (should have auto-created)")

        print("\n✨ Database seeding completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error seeding database: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()

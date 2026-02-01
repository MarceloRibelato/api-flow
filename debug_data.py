
from app.database import SessionLocal
from app.models.schedule_models import ScheduleModel
from app.models.feature_models import FeatureModel
from app.services.flow_service import FlowService

def debug_data(schedule_id):
    db = SessionLocal()
    try:
        print(f"--- Debugging Schedule {schedule_id} ---")
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            print("Schedule not found!")
            return

        print(f"Schedule Type: {schedule.type}")
        print(f"Target ID: {schedule.target_id}")


        if schedule.type == 'suite':
            product_id = schedule.target_id
            print(f"Schedule Company ID: {schedule.company_id}")
            
            # Verify Product Company
            from app.models.product_models import ProductModel
            product = db.query(ProductModel).filter(ProductModel.id == product_id).first()
            if product:
                 print(f"Product Company ID: {product.company_id}")
            
            features = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
            print(f"Found {len(features)} features for product {product_id}")
            
            for f in features:
                print(f"\nFeature: {f.name} (ID: {f.id})")
                flows = FlowService.list_by_project(db, f.id, schedule.company_id)
                print(f"  -> Found {len(flows)} flows")
        else:
             print("Not a suite schedule.")
    finally:
        db.close()

if __name__ == "__main__":
    debug_data(36)

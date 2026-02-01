
import logging
import sys
from app.database import SessionLocal
from app.services.scheduler_service import execute_job
from app.models.schedule_models import ScheduleModel
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
import datetime

# Setup explicit stdout logging
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(handler)

def debug_suite():
    db = SessionLocal()
    try:
        print("--- SETUP: Finding Product ---")
        # Find a product that HAS features
        from sqlalchemy.orm import aliased
        f_alias = aliased(FeatureModel)
        
        # Get product IDs with features
        product_id = db.query(FeatureModel.product_id).first()
        
        if not product_id:
            print("No features exist in DB to link to.")
            return

        product = db.query(ProductModel).filter(ProductModel.id == product_id[0]).first()
        if not product:
            print("Product found via feature but not in product table?")
            return

        print(f"Using Product: {product.name} (ID: {product.id})")
        
        # Check features
        features = db.query(FeatureModel).filter(FeatureModel.product_id == product.id).all()
        print(f"Product has {len(features)} features.")
        for f in features:
            print(f" - Feature: {f.name} (ID: {f.id})")

        if not features:
            print("No features, cannot test suite loop.")
            return

        # Create Dummy Schedule
        print("--- SETUP: Creating Dummy Schedule ---")
        schedule = ScheduleModel(
            name="Debug Suite Run",
            type="suite",
            target_id=product.id,
            status="active",
            company_id=product.company_id,
            notification_urls=""
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        print(f"Created Schedule ID: {schedule.id}")

        # Run Execution
        print("--- EXECUTION START ---")
        try:
            # execute_job creates its own DB session, so we don't pass 'db'
            execute_job(schedule.id)
            print("--- EXECUTION FINISHED ---")
        except Exception as e:
            print(f"--- EXECUTION CRASHED: {e} ---")
            import traceback
            traceback.print_exc()

        # Check Schedule Status
        db.refresh(schedule)
        print(f"Final Schedule Status: {schedule.last_run_status}")
        
        # Cleanup
        # db.delete(schedule)
        # db.commit()
        print("--- CLEANUP DONE ---")

    except Exception as e:
        print(f"Setup Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    debug_suite()

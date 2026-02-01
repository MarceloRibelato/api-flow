
import logging
from app.database import SessionLocal, engine
from app.models.product_models import ProductModel
from app.models.schedule_models import ScheduleModel
from app.models.feature_models import FeatureModel
from app.services.scheduler_service import execute_job
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_fallback_logic():
    db = SessionLocal()
    try:
        # 1. Create a dummy Product with Webhook
        logger.info("Creating dummy product...")
        prod = ProductModel(name="Test Webhook Product", notification_urls="https://echo.free.beeceptor.com")
        db.add(prod)
        db.commit()
        db.refresh(prod)
        logger.info(f"Created Product {prod.id} with webhook: {prod.notification_urls}")
        
        # 2. Create a dummy Schedule linked to Product (Suite)
        logger.info("Creating dummy schedule...")
        schedule = ScheduleModel(
            name="Test Webhook Schedule",
            type="suite",
            target_id=prod.id,
            status="active",
            company_id=1, # Assumption
            notification_urls="" # EMPTY to force fallback
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        logger.info(f"Created Schedule {schedule.id} with empty webhook")
        
        # 3. Simulate Logic (Partial extraction from execute_job)
        logger.info("Testing Fallback Logic...")
        
        # --- LOGIC COPY START ---
        target_urls = schedule.notification_urls
        if not target_urls:
            product_id_for_url = None
            if schedule.type == 'suite':
                product_id_for_url = schedule.target_id
            
            if product_id_for_url:
                p = db.query(ProductModel).filter(ProductModel.id == product_id_for_url).first()
                if p and p.notification_urls:
                    target_urls = p.notification_urls
                    logger.info(f"SUCCESS: Fallback found URL: {target_urls}")
                else:
                    logger.error("FAILURE: Fallback found product but NO webhook")
            else:
                logger.error("FAILURE: Could not determine product ID")
        else:
             logger.info("Schedule has URL, no fallback needed.")
        # --- LOGIC COPY END ---
        
        # Cleanup
        db.delete(schedule)
        db.delete(prod)
        db.commit()
        
    except Exception as e:
        logger.error(f"Test Failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    test_fallback_logic()

import logging
from sqlalchemy.orm import Session
from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, max_retries=3)
def celery_execute_job(self, schedule_id: int, failed_item_ids: list = None):
    """
    Celery task that executes a scheduled flow/suite/feature.
    """
    logger.info(f"Celery worker received schedule execution task: {schedule_id}, failed_item_ids: {failed_item_ids}")
    
    # Import locally to avoid circular dependencies during Celery initialization
    from app.services.scheduler_service import execute_job_logic
    
    try:
        execute_job_logic(schedule_id, failed_item_ids=failed_item_ids)
    except Exception as e:
        logger.error(f"Celery Task celery_execute_job failed for schedule {schedule_id}: {e}")
        # Retry with exponential backoff if desired, though typical test executions are idempotent
        # self.retry(exc=e, countdown=10)


@celery_app.task(bind=True)
def celery_run_load_test(
    self,
    job_id: str,
    api_list: list, 
    virtual_users: int, 
    duration_seconds: int, 
    ramp_up_seconds: int,
    company_id: int, 
    user_id: int,
    test_name: str = None,
    environment_id: int = None,
    dataset: list = None
):
    """
    Celery task that executes a performance load test.
    """
    logger.info(f"Celery worker received load test task: {job_id}")
    
    from app.services.performance_service import PerformanceService
    
    db: Session = SessionLocal()
    try:
        PerformanceService.run_load_test_sync(
            db=db,
            job_id=job_id,
            api_list=api_list,
            virtual_users=virtual_users,
            duration_seconds=duration_seconds,
            ramp_up_seconds=ramp_up_seconds,
            company_id=company_id,
            user_id=user_id,
            test_name=test_name,
            environment_id=environment_id,
            dataset=dataset
        )
    except Exception as e:
        logger.error(f"Celery Task celery_run_load_test failed for job {job_id}: {e}")
    finally:
        db.close()

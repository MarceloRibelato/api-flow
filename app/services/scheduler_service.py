from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import logging

from app.models.schedule_models import ScheduleModel
from app.models.api_test_history_models import ApiExecutionHistory
from app.database import SessionLocal
# Import services to execute logic - avoiding circular imports might be tricky
# ideally we move execution logic to a common place or import inside function

scheduler = BackgroundScheduler()

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def execute_job(schedule_id: int):
    """
    Callback function executed by the scheduler.
    """
    logger.info(f"Executing scheduled job: {schedule_id}")
    db = SessionLocal()
    try:
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found during execution")
            return

        schedule.last_run = datetime.now(timezone.utc)
        
        job = scheduler.get_job(str(schedule_id))
        if job:
             schedule.next_run = getattr(job, 'next_run_time', None)
        else:
             # Job is gone from scheduler (one-time job finished)
             schedule.next_run = None
             if not schedule.cron_expression:
                 schedule.status = 'running'
                 logger.info(f"Marking one-time schedule {schedule_id} as running")
        
        db.commit()

        # Import services locally
        from app.services.feature_service import FeatureService
        from app.services.flow_service import FlowService
        from app.services.history_service import HistoryService
        from app.services.variable_service import VariableService
        from app.services.flow_executor_service import FlowExecutorService
        from app.schemas.history_schemas import ExecutionHistoryCreate
        import requests
        import time
        import json

        # OPTIMIZATION: Use Session for connection pooling (Keep-Alive)
        # This significantly reduces latency by handling TCP/SSL once.
        session = requests.Session()
        # CRITICAL FIX: Disable proxy detection (can cause 1-2s delay on Windows)
        session.trust_env = False



        # --- EXECUTION LOGIC ---
        success_count = 0
        fail_count = 0

        if schedule.type == 'feature':
            feature_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Feature {feature_id} in Env {env_id}")
            
            s, f = FlowExecutorService.execute_feature_group(
                db, feature_id, env_id, schedule.company_id, 
                schedule_id=schedule.id, user_id=schedule.user_id or 1,
                flow_type=getattr(schedule, 'flow_type', 'api'),
                capture_video=getattr(schedule, 'capture_video', False),
                capture_screenshot=getattr(schedule, 'capture_screenshot', False)
            )
            success_count = s
            fail_count = f

            # Determine overall status
            schedule.last_run_status = 'failure' if fail_count > 0 else 'success'
            db.commit()


        elif schedule.type == 'flow':
            flow_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Flow {flow_id} in Env {env_id}")
            
            s, f = FlowExecutorService.execute_flow_by_id(
                db, flow_id, env_id, schedule.company_id, 
                schedule_id=schedule.id, user_id=schedule.user_id or 1,
                flow_type=getattr(schedule, 'flow_type', 'api'),
                capture_video=getattr(schedule, 'capture_video', False),
                capture_screenshot=getattr(schedule, 'capture_screenshot', False)
            )
            success_count = s
            fail_count = f

            # Determine overall status
            schedule.last_run_status = 'failure' if fail_count > 0 else 'success'
            db.commit()

        elif schedule.type == 'suite':
            product_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Suite (Product) {product_id} in Env {env_id}")
            
            # Extract concurrency from schedule (if any)
            concurrency = getattr(schedule, 'max_concurrency', None)
            
            s, f = FlowExecutorService.execute_suite(
                db, product_id, env_id, schedule.company_id, 
                schedule_id=schedule.id, user_id=schedule.user_id or 1,
                max_concurrency=schedule.max_concurrency,
                flow_type=getattr(schedule, 'flow_type', 'api'),
                capture_video=getattr(schedule, 'capture_video', False),
                capture_screenshot=getattr(schedule, 'capture_screenshot', False)
            )
            success_count = s
            fail_count = f
            
            # Update Schedule Status
            schedule.last_run_status = 'failure' if fail_count > 0 else 'success'
            db.commit()

        # Mark one-time schedule as actually completed
        if not schedule.cron_expression:
            schedule.status = 'completed'
            db.commit()

        # --- 4. Send Webhook Notification ---
        # Logic: Use Schedule URL > Fallback to Product URL
        
        target_urls = schedule.notification_urls
        
        if not target_urls:
            # Fallback logic based on type
            try:
                from app.models.product_models import ProductModel
                from app.models.feature_models import FeatureModel
                
                product_id_for_url = None
                
                if schedule.type == 'suite':
                    product_id_for_url = schedule.target_id
                elif schedule.type == 'feature':
                    # Need to get product_id from feature
                    feat = db.query(FeatureModel).filter(FeatureModel.id == schedule.target_id).first()
                    if feat:
                        product_id_for_url = feat.product_id
                elif schedule.type == 'flow':
                    # Need to get product_id from flow -> feature -> product
                    from app.models.flow_models import FlowDB
                    flow_obj = db.query(FlowDB).filter(FlowDB.id == schedule.target_id).first()
                    if flow_obj:
                         feat = db.query(FeatureModel).filter(FeatureModel.id == flow_obj.project_id).first()
                         if feat:
                            product_id_for_url = feat.product_id
                
                if product_id_for_url:
                    prod = db.query(ProductModel).filter(ProductModel.id == product_id_for_url).first()
                    if prod and prod.notification_urls:
                        target_urls = prod.notification_urls
                        logger.info(f"Using Product-level webhook for Schedule {schedule.id}")

            except Exception as e:
                logger.error(f"Error fetching product webhook: {e}")

        if target_urls:
            # CHECK NOTIFICATION TOGGLE
            if hasattr(schedule, 'notifications_enabled') and schedule.notifications_enabled is False:
                 logger.info(f"Skipping webhook notification for Schedule {schedule.id} (notifications disabled)")
                 # We still want to log or do other things? Probably just skip.
            else:
                logger.info(f"Target URLs for webhook: {target_urls}")
                try:
                    # Helper function defined inline or use a service method if reusable
                    # Using simple requests here for immediate execution
                    urls = [u.strip() for u in target_urls.split(',') if u.strip()]
                    logger.info(f"Parsed URLs: {urls}")
                    
                    # Use UTC-3 for execution time in notifications
                    from datetime import timedelta
                    tz_adjust = timedelta(hours=3)
                    exec_time_dt = schedule.last_run if schedule.last_run else datetime.now(timezone.utc)
                    br_time = (exec_time_dt - tz_adjust).strftime("%d/%m/%Y, %H:%M:%S")

                    payload = {
                        "schedule_id": schedule.id,
                        "schedule_name": schedule.name,
                        "type": schedule.type,
                        "target_id": schedule.target_id,
                        "status": schedule.last_run_status,
                        "execution_time": br_time,
                        "success_count": success_count,
                        "fail_count": fail_count
                    }
                
                    for url in urls:
                        logger.info(f"Sending webhook to: {url}")
                        
                        try:
                            final_payload = payload
                            headers = {'Content-Type': 'application/json'}

                            # --- SLACK FORMATTING ---
                            if 'hooks.slack.com' in url:
                                color = "#36a64f" if payload['status'] == 'success' else "#d72b3f"
                                final_payload = {
                                    "text": f"Execution Report: {payload['schedule_name']}",
                                    "attachments": [
                                        {
                                            "color": color,
                                            "fields": [
                                                {"title": "Status", "value": payload['status'].upper(), "short": True},
                                                {"title": "Success", "value": str(payload['success_count']), "short": True},
                                                {"title": "Failed", "value": str(payload['fail_count']), "short": True},
                                                {"title": "Target", "value": f"{payload['type'].title()} #{payload['target_id']}", "short": True}
                                            ],
                                            "footer": "API Flow Scheduler",
                                            "ts": int(time.time())
                                        }
                                    ]
                                }
                            
                            # --- TEAMS FORMATTING (Adaptive Card or MessageCard) ---
                            elif 'webhook.office.com' in url or 'outlook.office.com' in url:
                                theme_color = "00FF00" if payload['status'] == 'success' else "FF0000"
                                final_payload = {
                                    "@type": "MessageCard",
                                    "@context": "http://schema.org/extensions",
                                    "themeColor": theme_color,
                                    "summary": f"Execution: {payload['schedule_name']}",
                                    "sections": [{
                                        "activityTitle": f"📢 Execution Completed: {payload['schedule_name']}",
                                        "activitySubtitle": f"Status: {payload['status'].upper()}",
                                        "facts": [
                                        {"name": "Success", "value": str(payload['success_count'])},
                                        {"name": "Failed", "value": str(payload['fail_count'])},
                                        {"name": "Time", "value": payload['execution_time']}
                                    ],
                                    "markdown": True
                                }]
                            }

                            wh_resp = requests.post(url, json=final_payload, headers=headers, timeout=5)
                            logger.info(f"Webhook response: {wh_resp.status_code}")
                        except Exception as wh_err:
                            logger.error(f"Failed to send webhook to {url}: {wh_err}")

                except Exception as notify_err:
                    logger.error(f"Error processing webhooks: {notify_err}")

    except Exception as outer_e:
        logger.error(f"Critical error in execute_job {schedule_id}: {outer_e}")
        # Try to set status to failure if DB session is still viable
        try:
             schedule.last_run_status = 'failure'
             # Essential: Mark one-time schedule as failed so frontend stops polling
             if not getattr(schedule, 'cron_expression', None):
                 schedule.status = 'failure'
             db.commit()
        except: pass
    finally:
        # Close the session to free resources
        if 'session' in locals():
            session.close()
        db.close()

class SchedulerService:
    def __init__(self):
        self.scheduler = scheduler

    def start(self):
        if not scheduler.running:
            scheduler.start()
            logger.info("APScheduler started")
            
            # Register Maintenance Jobs
            self.add_maintenance_jobs()

    def add_maintenance_jobs(self):
        """
        Registers non-user scheduled maintenance tasks.
        """
        # 1. History Cleanup (Daily at 03:00 AM)
        if not scheduler.get_job('maintenance_purge_history'):
            scheduler.add_job(
                purge_history_job,
                trigger=CronTrigger(hour=3, minute=0),
                id='maintenance_purge_history',
                replace_existing=True
            )
            logger.info("Registered daily history purge job (03:00 AM)")

    def add_job(self, schedule: ScheduleModel, db: Session):
        """
        Adds a job to the scheduler based on the ScheduleModel.
        """
        job_id = str(schedule.id)

        # Remove existing if any (to update)
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if schedule.status != 'active':
            return

        trigger = None
        if schedule.cron_expression:
            trigger = CronTrigger.from_crontab(schedule.cron_expression)
        elif schedule.run_at:
            trigger = DateTrigger(run_date=schedule.run_at)

        if trigger:
            scheduler.add_job(
                execute_job,
                trigger=trigger,
                args=[schedule.id],
                id=job_id,
                replace_existing=True
            )

            # Update next_run immediately
            job = scheduler.get_job(job_id)
            if job:
                schedule.next_run = getattr(job, "next_run_time", None)
                db.commit()

    def remove_job(self, schedule_id: int):
        job_id = str(schedule_id)
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    def sync_jobs(self, db: Session):
        """
        Syncs in-memory scheduler with database schedules (e.g. on startup)
        """
        schedules = db.query(ScheduleModel).filter(ScheduleModel.status == 'active').all()
        for s in schedules:
            self.add_job(s, db)


def purge_history_job():
    """
    Tiered maintenance task: 
    1. Archive records older than HISTORY_RETENTION_DAYS (30)
    2. Purge archived records older than HISTORY_ARCHIVE_RETENTION_DAYS (180)
    """
    from app.services.history_service import HistoryService
    from app.config import settings
    from app.database import SessionLocal
    
    db = SessionLocal()
    try:
        # Tier 1: Archive
        retain_days = settings.HISTORY_RETENTION_DAYS
        logger.info(f"💾 Starting history archiving (Threshold: {retain_days} days)")
        archived_count = HistoryService.archive_old_records(db, retain_days)
        logger.info(f"✅ Archived {archived_count} records.")
        
        # 🔗 Cleanup Files associated with Archived Batches
        if archived_count > 0:
            import os
            import shutil
            BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            VIDEO_ROOT = os.path.join(BASE_DIR, "data", "videos")
            
            # Use same threshold as Tier 1 Archive
            threshold = datetime.now(timezone.utc) - timedelta(days=retain_days)
            
            # Find unique batch IDs that were archived
            archived_batches = db.query(ApiExecutionHistory.batch_id).filter(ApiExecutionHistory.created_at < threshold).distinct().all()
            for (batch_id,) in archived_batches:
                if batch_id:
                    batch_path = os.path.join(VIDEO_ROOT, batch_id)
                    if os.path.exists(batch_path):
                        logger.info(f"🧹 Deleting archived recording: {batch_path}")
                        shutil.rmtree(batch_path, ignore_errors=True)
        
        # Tier 2: Purge
        archive_retain_days = settings.HISTORY_ARCHIVE_RETENTION_DAYS
        logger.info(f"🧹 Starting archive purge (Threshold: {archive_retain_days} days)")
        purged_count = HistoryService.purge_archived_records(db, archive_retain_days)
        logger.info(f"✨ Purge complete. Removed {purged_count} archived records.")
        
    except Exception as e:
        logger.error(f"❌ Failed to run history maintenance: {e}")
    finally:
        db.close()

scheduler_service = SchedulerService()

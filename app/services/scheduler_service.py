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

# Optimized Scheduler Configuration
job_defaults = {
    'misfire_grace_time': 60,  # Allow up to 60s delay (crucial for container/Windows stability)
    'coalesce': True,          # Combine missed runs into one
    'max_instances': 3         # Allow 3 instances of the same job simultaneously
}

scheduler = BackgroundScheduler(job_defaults=job_defaults)

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def execute_job_logic(schedule_id: int):
    """
    Original callback function now executed by Celery worker.
    """
    logger.info(f"Executing scheduled job logic: {schedule_id}")
    db = SessionLocal()
    
    success_count = 0
    fail_count = 0
    schedule = None
    
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
        if getattr(schedule, 'flow_type', 'api') == 'performance':
            logger.info(f"🚀 Running Scheduled PERFORMANCE Test for {schedule.type} {schedule.target_id}")
            from app.services.performance_service import PerformanceService
            import uuid
            
            job_id = f"perf_{uuid.uuid4().hex}"
            
            # Helper to extract API list from a flow
            def extract_apis_from_flow(flow_id, proj_id):
                flow_data = FlowService.load(db, proj_id, schedule.company_id, flow_id)
                cards = flow_data.get('cardData', {})
                nodes = flow_data.get('nodes', [])
                
                # Simple extraction, respecting node order might be complex, 
                # but for load testing we often just want the list. 
                # Let's sort by x position roughly.
                nodes.sort(key=lambda n: n.get('position', {}).get('x', 0))
                
                api_list = []
                for n in nodes:
                    c = cards.get(n['id'])
                    if c and c.get('apiCalls'):
                        for api in c['apiCalls']:
                            api_list.append(api)
                return api_list

            final_api_list = []
            
            if schedule.type == 'flow':
                from app.models.flow_models import FlowDB
                f_db = db.query(FlowDB).filter(FlowDB.id == schedule.target_id).first()
                if f_db and getattr(f_db, 'flow_type', 'api') == 'api':
                    final_api_list = extract_apis_from_flow(f_db.id, f_db.project_id)
            elif schedule.type == 'feature':
                # Get the first API flow of the feature to load test
                flows = FlowService.list_by_project(db, schedule.target_id, schedule.company_id)
                api_flows = [f for f in flows if f.get('flow_type') == 'api']
                if api_flows:
                    final_api_list = extract_apis_from_flow(api_flows[0]['id'], schedule.target_id)
            elif schedule.type == 'suite':
                # Extract APIs from all API flows under all features of this product (target_id is product_id)
                from app.models.feature_models import FeatureModel
                features = db.query(FeatureModel).filter(FeatureModel.product_id == schedule.target_id).all()
                for feat in features:
                    flows = FlowService.list_by_project(db, feat.id, schedule.company_id)
                    for flow_obj in flows:
                        if flow_obj.get('flow_type') == 'api':
                            final_api_list.extend(extract_apis_from_flow(flow_obj['id'], feat.id))
            
            if final_api_list:
                from app.models.performance_models import PerformanceTestResult
                
                target_url = final_api_list[0].get("url", "unknown_url") if len(final_api_list) == 1 else "Multiple APIs (Flow)"
                
                # Resolve environment ID fallback if not set
                env_id = schedule.environment_id
                if not env_id:
                    product_id = None
                    if schedule.type == 'suite':
                        product_id = schedule.target_id
                    elif schedule.type == 'feature':
                        from app.models.feature_models import FeatureModel
                        feat = db.query(FeatureModel).filter(FeatureModel.id == schedule.target_id).first()
                        if feat:
                            product_id = feat.product_id
                    elif schedule.type == 'flow':
                        from app.models.flow_models import FlowDB
                        from app.models.feature_models import FeatureModel
                        f_db = db.query(FlowDB).filter(FlowDB.id == schedule.target_id).first()
                        if f_db:
                            feat = db.query(FeatureModel).filter(FeatureModel.id == f_db.project_id).first()
                            if feat:
                                product_id = feat.product_id
                    
                    if product_id:
                        from app.services.environment_service import EnvironmentService
                        envs = EnvironmentService.get_by_project(db, product_id)
                        if envs:
                            env_id = envs[0].id
                            logger.info(f"Resolved fallback environment ID {env_id} for scheduled performance test {schedule.id}")

                # Create result record
                perf_result = PerformanceTestResult(
                    id=job_id,
                    company_id=schedule.company_id,
                    user_id=schedule.user_id or 1,
                    status="pending",
                    virtual_users=schedule.virtual_users or 10,
                    duration_seconds=schedule.duration_seconds or 30,
                    ramp_up_seconds=schedule.ramp_up_seconds or 0,
                    test_name=f"Schedule {schedule.id}: {schedule.name}" if schedule.name else f"Scheduled Perf Test {schedule.id}",
                    target_url=target_url
                )
                db.add(perf_result)
                db.commit()
                
                from app.tasks.execution_tasks import celery_run_load_test
                celery_run_load_test.delay(
                    job_id=job_id,
                    api_list=final_api_list,
                    virtual_users=schedule.virtual_users or 10,
                    duration_seconds=schedule.duration_seconds or 30,
                    ramp_up_seconds=schedule.ramp_up_seconds or 0,
                    company_id=schedule.company_id,
                    user_id=schedule.user_id or 1,
                    test_name=perf_result.test_name,
                    environment_id=env_id
                )
                schedule.last_run_status = 'success' # Indicates it was triggered successfully
                db.commit()
            else:
                logger.error(f"Failed to extract APIs for performance test schedule {schedule.id}")
                schedule.last_run_status = 'failure'
                db.commit()

        elif schedule.type == 'feature':
            feature_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Feature {feature_id} in Env {env_id}")
            
            s, f = FlowExecutorService.execute_feature_group(
                db, feature_id, env_id, schedule.company_id, 
                schedule_id=schedule.id, user_id=schedule.user_id or 1,
                max_concurrency=getattr(schedule, 'max_concurrency', None),
                flow_type=getattr(schedule, 'flow_type', 'api'),
                capture_video=getattr(schedule, 'capture_video', False),
                capture_screenshot=getattr(schedule, 'capture_screenshot', False),
                visible_execution=getattr(schedule, 'visible_execution', False)
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
                max_concurrency=getattr(schedule, 'max_concurrency', None),
                flow_type=getattr(schedule, 'flow_type', 'api'),
                capture_video=getattr(schedule, 'capture_video', False),
                capture_screenshot=getattr(schedule, 'capture_screenshot', False),
                visible_execution=getattr(schedule, 'visible_execution', False)
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
                capture_screenshot=getattr(schedule, 'capture_screenshot', False),
                visible_execution=getattr(schedule, 'visible_execution', False)
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

    except Exception as e:
        logger.error(f"Error executing job logic {schedule_id}: {e}")
        import traceback
        traceback.print_exc()
        if schedule:
            try:
                schedule.last_run_status = 'failure'
                # Essential: Mark one-time schedule as failed so frontend stops polling
                if not getattr(schedule, 'cron_expression', None):
                    schedule.status = 'failure'
                db.commit()
            except Exception as db_err:
                logger.error(f"Failed to set failure status in DB: {db_err}")
                db.rollback()

    finally:
        # Close the session to free resources
        if 'session' in locals():
            try:
                session.close()
            except:
                pass

    # --- 4. Send Webhook Notification ---
    # Logic: Use Schedule URL > Fallback to Product URL
    if schedule:
        try:
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

                                import requests
                                wh_resp = requests.post(url, json=final_payload, headers=headers, timeout=5)
                                logger.info(f"Webhook response: {wh_resp.status_code}")
                            except Exception as wh_err:
                                logger.error(f"Failed to send webhook to {url}: {wh_err}")

                    except Exception as notify_err:
                        logger.error(f"Error processing webhooks: {notify_err}")
        except Exception as notify_outer_err:
            logger.error(f"Outer error in notifications dispatch: {notify_outer_err}")
        finally:
            db.close()

def execute_job(schedule_id: int):
    """
    Callback function executed by the scheduler. Pushes the actual work to Celery.
    """
    logger.info(f"APScheduler pushing job {schedule_id} to Celery")
    from app.tasks.execution_tasks import celery_execute_job
    celery_execute_job.delay(schedule_id)

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
                replace_existing=True,
                misfire_grace_time=60
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

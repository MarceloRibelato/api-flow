from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from app.models.schedule_models import ScheduleModel
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
    Fetches the schedule details and triggers the actual test execution.
    """
    logger.info(f"Executing scheduled job: {schedule_id}")
    db = SessionLocal()
    try:
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found during execution")
            return

        schedule.last_run = datetime.utcnow()
        if scheduler.get_job(str(schedule_id)):
             schedule.next_run = scheduler.get_job(str(schedule_id)).next_run_time
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

        # Helper to prepare variables
        def get_merged_variables(product_id, env_id):
            global_vars = VariableService.get_all(db, product_id, environment_id=None)
            env_vars = []
            if env_id:
                env_vars = VariableService.get_all(db, product_id, environment_id=env_id)
            
            variables = {v.name: v for v in global_vars}
            for v in env_vars:
                variables[v.name] = v
            return variables

        # --- EXECUTION LOGIC ---
        success_count = 0
        fail_count = 0

        if schedule.type == 'feature':
            feature_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Feature {feature_id} in Env {env_id}")

            feature = FeatureService.get_by_id(db, feature_id, schedule.company_id)
            if not feature:
                logger.error(f"Feature {feature_id} not found")
                return

            # Fix: list_by_project uses project_id which corresponds to Feature ID in FlowDB
            flows = FlowService.list_by_project(db, feature.id, schedule.company_id)
            
            # Prepare variables
            variables = get_merged_variables(feature.product_id, env_id)

            # Prepare variables
            variables = get_merged_variables(feature.product_id, env_id)

            for flow_meta in flows:
                s, f = FlowExecutorService.execute_flow_logic(db, flow_meta, feature.product_id, env_id, schedule.company_id, variables, feature_name=feature.name, schedule_id=schedule.id, user_id=schedule.user_id if schedule.user_id else 1)
                success_count += s
                fail_count += f
            
            # Determine overall status
            if fail_count > 0:
                schedule.last_run_status = 'failure'
            else:
                schedule.last_run_status = 'success'
            
            logger.info(f"Feature execution completed. Success: {success_count}, Fail: {fail_count}. Status: {schedule.last_run_status}")
            db.commit()

        elif schedule.type == 'suite':
            product_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Suite (Product) {product_id} in Env {env_id}")
            
            # 1. Fetch all features for the product
            # Needs to import FeatureModel here or use Service if available
            from app.models.feature_models import FeatureModel
            feature_objs = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
            
            # Detach data to avoid session issues during loop commits/rollbacks
            features = [{'id': f.id, 'name': f.name, 'product_id': f.product_id} for f in feature_objs]
            
            if not features:
                logger.warning(f"No features found for product {product_id}")
                return
            
            logger.info(f"Found {len(features)} features for product {product_id}")

            # 2. Prepare variables once for the product
            variables = get_merged_variables(product_id, env_id)

            # 3. Iterate features and execute their flows

            for feature in features:
                try:
                    logger.info(f"  📂 Processing Feature: {feature['name']} (ID: {feature['id']})")
                    flows = FlowService.list_by_project(db, feature['id'], schedule.company_id)
                    
                    if not flows:
                        logger.info(f"     (No flows found)")
                        continue

                    for flow_meta in flows:
                        s, f = FlowExecutorService.execute_flow_logic(db, flow_meta, product_id, env_id, schedule.company_id, variables, feature_name=feature['name'], schedule_id=schedule.id, user_id=schedule.user_id if schedule.user_id else 1)
                        success_count += s
                        fail_count += f
                
                except Exception as feat_ex:
                    logger.error(f"Failed to process feature {feature['id']}: {feat_ex}")
                    db.rollback() # Ensure session is clean for next feature
                    # Optionally count as failure or just log
                    fail_count += 1 # Assume failure if feature crashes
            
            # Update Schedule Status (Last Run Status)
            if fail_count > 0:
                schedule.last_run_status = 'failure'
            else:
                schedule.last_run_status = 'success'
            
            logger.info(f"Suite execution completed. Success: {success_count}, Fail: {fail_count}. Status: {schedule.last_run_status}")
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
                    
                    payload = {
                        "schedule_id": schedule.id,
                        "schedule_name": schedule.name,
                        "type": schedule.type,
                        "target_id": schedule.target_id,
                        "status": schedule.last_run_status,
                        "execution_time": schedule.last_run.isoformat() if schedule.last_run else datetime.utcnow().isoformat(),
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
             db.commit()
        except: pass
    finally:
        # Close the session to free resources
        if 'session' in locals():
            session.close()
        db.close()

class SchedulerService:
    def start(self):
        if not scheduler.running:
            scheduler.start()
            logger.info("APScheduler started")

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
            # Assuming cron string like "0 9 * * *" or 5 fields
            # Simplified: Use cron trigger. Ideally parse the string.
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
                schedule.next_run = job.next_run_time
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

scheduler_service = SchedulerService()

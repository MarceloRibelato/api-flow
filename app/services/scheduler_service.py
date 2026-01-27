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
        from app.schemas.history_schemas import ExecutionHistoryCreate
        import requests
        import time
        import json

        # OPTIMIZATION: Use Session for connection pooling (Keep-Alive)
        # This significantly reduces latency by handling TCP/SSL once.
        session = requests.Session()
        # CRITICAL FIX: Disable proxy detection (can cause 1-2s delay on Windows)
        session.trust_env = False

        # Helper method for replacing variables - simplified version
        def replace_vars(text, variables):
            if not text or not isinstance(text, str): return text
            for k, v in variables.items():
                if v and v.value:
                    text = text.replace(f"{{{{{v.name}}}}}", str(v.value))
            return text

        # Helper to execute a single flow
        def execute_flow_logic(flow_meta, product_id, env_id, variables_dict):
            try:
                # Load full flow data with cards
                flow_data = FlowService.load(db, flow_meta['project_id'], flow_meta['id'])
                cards = flow_data.get('cardData', {})
                
                logger.info(f"   ► Executing Flow: {flow_meta.get('name', 'Unknown')} (ID: {flow_meta['id']}) - {len(cards)} cards")

                for card_id, card in cards.items():
                    api_calls = card.get('apiCalls', [])
                    for api_call in api_calls:
                        try:
                            # Prepare Request
                            method = api_call.get('method', 'GET')
                            url = replace_vars(api_call.get('url', ''), variables_dict)
                            
                            # FORCE FIX: Replace localhost with 127.0.0.1
                            if 'localhost' in url:
                                url = url.replace('localhost', '127.0.0.1')
                            
                            headers_raw = api_call.get('headers', {})
                            if isinstance(headers_raw, list):
                                mapping = {}
                                for h in headers_raw:
                                     if isinstance(h, dict):
                                         if 'key' in h and 'value' in h:
                                             mapping[h['key']] = h['value']
                                         else:
                                             mapping.update(h)
                                headers_raw = mapping

                            headers_json_str = json.dumps(headers_raw)
                            headers = json.loads(replace_vars(headers_json_str, variables_dict))
                            
                            if isinstance(headers, list): headers = {}
                            if not isinstance(headers, dict) and headers is not None: headers = {}

                            body = replace_vars(api_call.get('body', ''), variables_dict)
                            
                            start_time = time.time()
                            try:
                                resp = session.request(method, url, headers=headers, data=body)
                            except Exception as req_ex:
                                logger.error(f"Request failed: {req_ex}")
                                raise req_ex
                            duration = int((time.time() - start_time) * 1000)
                            
                            # Save History
                            hist = ExecutionHistoryCreate(
                                api_id=api_call.get('id'),
                                api_name=api_call.get('name'),
                                project_id=product_id,
                                flow_id=flow_meta['id'],
                                node_name=card.get('name'),
                                method=method,
                                url=url,
                                request_headers=headers,
                                request_body=body,
                                status_code=resp.status_code,
                                status_text=resp.reason,
                                response_headers=dict(resp.headers),
                                response_body=resp.text,
                                response_time=duration,
                                environment_id=env_id,
                                environment_name=str(env_id),
                                variables_used={}
                            )
                            user_id_to_save = schedule.user_id if schedule.user_id else 1
                            HistoryService.save(db, hist, user_id=user_id_to_save)
                            logger.info(f"      -> {method} {url} [{resp.status_code}]")
                        except Exception as ex:
                             logger.error(f"Failed to execute API call in card {card.get('name')}: {ex}")
            except Exception as e:
                logger.error(f"Error executing flow {flow_meta['id']}: {e}")

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

        if schedule.type == 'feature':
            feature_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Feature {feature_id} in Env {env_id}")

            feature = FeatureService.get_by_id(db, feature_id)
            if not feature:
                logger.error(f"Feature {feature_id} not found")
                return

            # Fix: list_by_project uses project_id which corresponds to Feature ID in FlowDB
            flows = FlowService.list_by_project(db, feature.id)
            
            # Prepare variables
            variables = get_merged_variables(feature.product_id, env_id)

            for flow_meta in flows:
                execute_flow_logic(flow_meta, feature.product_id, env_id, variables)

        elif schedule.type == 'suite':
            product_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Suite (Product) {product_id} in Env {env_id}")
            
            # 1. Fetch all features for the product
            # Needs to import FeatureModel here or use Service if available
            from app.models.feature_models import FeatureModel
            features = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
            
            if not features:
                logger.warning(f"No features found for product {product_id}")
                return

            # 2. Prepare variables once for the product
            variables = get_merged_variables(product_id, env_id)

            # 3. Iterate features and execute their flows
            for feature in features:
                logger.info(f"  📂 Processing Feature: {feature.name} (ID: {feature.id})")
                flows = FlowService.list_by_project(db, feature.id)
                
                if not flows:
                    logger.info(f"     (No flows found)")
                    continue

                for flow_meta in flows:
                    execute_flow_logic(flow_meta, product_id, env_id, variables)
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
            # For this MVP, let's assume standard cron format or support simple interval?
            # User request said "date/time OR daily at specific time".
            # Daily at X: handled by cron. 
            # Date/Time: handled by run_at.
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

import asyncio
import uuid
import logging
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from app.models.product_models import ProductModel

logger = logging.getLogger(__name__)

class ExecutorDispatcher:
    @staticmethod
    async def dispatch(
        db: Session,
        flow_data: Dict[str, Any],
        user_id: int,
        project_id: int,
        env_id: Optional[int] = None,
        schedule_id: Optional[int] = None,
        company_id: Optional[int] = None,
        flow_session: Any = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        capture_video: bool = False,
        capture_screenshot: bool = False,
        global_vars: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Dispatches the flow execution to the appropriate specialized executor
        based on the flow_type or node types.
        """
        flow_type = flow_data.get('flow_type', 'api')
        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        
        # Determine execution engine
        if flow_type == 'e2e' or flow_type == 'web':
            from app.services.executors.web_executor_service import WebExecutorService
            logger.info(f"🚀 Dispatching flow {flow_data.get('id')} to Web/E2E Executor")
            return await WebExecutorService.execute(
                db=db, flow_data=flow_data, user_id=user_id, project=project_id,
                env_id=env_id, schedule_id=schedule_id, company_id=company_id,
                batch_id=batch_id, capture_video=capture_video, capture_screenshot=capture_screenshot,
                global_vars=global_vars
            )
            
        elif flow_type == 'mobile':
            from app.services.executors.mobile_executor_service import MobileExecutorService
            logger.info(f"🚀 Dispatching flow {flow_data.get('id')} to Mobile Executor")
            return await MobileExecutorService.execute(
                db=db, flow_data=flow_data, user_id=user_id, project=project_id,
                env_id=env_id, schedule_id=schedule_id, company_id=company_id,
                batch_id=batch_id, capture_video=capture_video, capture_screenshot=capture_screenshot,
                global_vars=global_vars
            )
            
        else:
            # Default to API
            from app.services.executors.api_executor_service import ApiExecutorService
            logger.info(f"🚀 Dispatching flow {flow_data.get('id')} to API Executor")
            return await ApiExecutorService.execute(
                db=db, flow_data=flow_data, user_id=user_id, project=project_id,
                env_id=env_id, schedule_id=schedule_id, company_id=company_id,
                flow_session=flow_session, loop=loop, batch_id=batch_id,
                global_vars=global_vars
            )

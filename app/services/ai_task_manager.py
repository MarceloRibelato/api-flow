import uuid
import datetime
import threading
import logging
from typing import Dict, Any, Optional
from app.database import SessionLocal

logger = logging.getLogger(__name__)

class AiTaskManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AiTaskManager, cls).__new__(cls)
                    cls._instance._tasks: Dict[str, Dict[str, Any]] = {}
        return cls._instance

    def get_active_task_for_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Returns any active (PENDING or PROCESSING) task for the given user_id."""
        with self._lock:
            for task_id, task in self._tasks.items():
                if task.get("user_id") == user_id and task.get("status") in ["PENDING", "PROCESSING"]:
                    return self._clean_task_copy(task)
        return None

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns task metadata and result if completed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return self._clean_task_copy(task)
        return None

    def cancel_task(self, task_id: str, user_id: int) -> bool:
        """Marks a task as CANCELLED for the given user."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.get("user_id") == user_id:
                if task.get("status") in ["PENDING", "PROCESSING"]:
                    task["cancel_requested"] = True
                    task["status"] = "CANCELLED"
                    task["error"] = "Solicitação cancelada pelo usuário."
                    logger.info(f"AI Task {task_id} marked as CANCELLED by user {user_id}")
                    return True
        return False

    def start_task(self, user_id: int, flow_id: int, skill_id: str, runner_func, *args, **kwargs) -> Dict[str, Any]:
        """Starts a background AI generation task if no active task exists for the user."""
        active = self.get_active_task_for_user(user_id)
        if active:
            return active

        task_id = f"ai_task_{uuid.uuid4().hex[:12]}"
        task_data = {
            "task_id": task_id,
            "user_id": user_id,
            "flow_id": flow_id,
            "skill_id": skill_id,
            "status": "PENDING",
            "cancel_requested": False,
            "result": None,
            "error": None,
            "created_at": datetime.datetime.utcnow().isoformat()
        }

        with self._lock:
            self._tasks[task_id] = task_data

        thread = threading.Thread(
            target=self._run_worker,
            args=(task_id, runner_func, args, kwargs),
            daemon=True
        )
        thread.start()
        logger.info(f"Started AI Task {task_id} for user {user_id}, flow {flow_id}, skill {skill_id}")
        return self._clean_task_copy(task_data)

    def _run_worker(self, task_id: str, runner_func, args, kwargs):
        db = SessionLocal()
        try:
            with self._lock:
                task = self._tasks.get(task_id)
                if not task or task.get("cancel_requested"):
                    if task:
                        task["status"] = "CANCELLED"
                    return
                task["status"] = "PROCESSING"

            logger.info(f"Executing worker for AI Task {task_id}...")
            # Execute worker function passing DB session
            res = runner_func(db, *args, **kwargs)

            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                if task.get("cancel_requested"):
                    task["status"] = "CANCELLED"
                    task["error"] = "Solicitação cancelada pelo usuário."
                    logger.info(f"AI Task {task_id} completed work but was CANCELLED.")
                else:
                    task["status"] = "COMPLETED"
                    task["result"] = res
                    logger.info(f"AI Task {task_id} COMPLETED successfully.")

        except Exception as e:
            logger.error(f"Error executing worker for AI Task {task_id}: {e}", exc_info=True)
            with self._lock:
                task = self._tasks.get(task_id)
                if task and not task.get("cancel_requested"):
                    task["status"] = "FAILED"
                    err_msg = str(e)
                    if "Read timed out" in err_msg or "timeout" in err_msg.lower():
                        err_msg = "O servidor de IA demorou muito para responder (Timeout). Tente novamente ou verifique se o serviço de IA está ativo."
                    task["error"] = err_msg
        finally:
            db.close()

    def _clean_task_copy(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": task.get("task_id"),
            "user_id": task.get("user_id"),
            "flow_id": task.get("flow_id"),
            "skill_id": task.get("skill_id"),
            "status": task.get("status"),
            "cancel_requested": task.get("cancel_requested", False),
            "result": task.get("result"),
            "error": task.get("error"),
            "created_at": task.get("created_at")
        }

ai_task_manager = AiTaskManager()

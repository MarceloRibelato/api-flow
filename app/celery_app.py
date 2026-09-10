import os
from celery import Celery
from app.utils.logger import setup_logging

setup_logging()

# Initialize Celery app
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "flow_worker",
    broker=broker_url,
    backend=result_backend,
    include=["app.tasks.execution_tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=4,  # Adjust based on server capacity
    worker_prefetch_multiplier=1,
)

from celery import Celery
from app.config import settings

celery_app = Celery(
    "socrates",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.outreach",
        "app.workers.memory_index",
        "app.workers.analytics",
        "app.workers.email",
        "app.workers.research",
        "app.workers.report_gen",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_queues={
        "critical": {"exchange": "default", "routing_key": "critical"},
        "high": {"exchange": "default", "routing_key": "high"},
        "default": {"exchange": "default", "routing_key": "default"},
        "low": {"exchange": "default", "routing_key": "low"},
        "batch": {"exchange": "default", "routing_key": "batch"},
    },
    task_routes={
        "app.workers.outreach.*": {"queue": "high"},
        "app.workers.memory_index.*": {"queue": "low"},
        "app.workers.analytics.*": {"queue": "default"},
        "app.workers.email.*": {"queue": "default"},
        "app.workers.research.*": {"queue": "low"},
        "app.workers.report_gen.*": {"queue": "default"},
    },
)

"""Celery application instance.

Workers are started with:
    celery -A app.celery_app worker --loglevel=info
Beat scheduler:
    celery -A app.celery_app beat --loglevel=info
"""
from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "medecinai",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.transcription",
        "app.tasks.embedding",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Paris",
    enable_utc=True,
    # Retry configuration
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Result expiry: 24h
    result_expires=86400,
    # Route AI tasks to a dedicated queue (can be scaled independently)
    task_routes={
        "app.tasks.transcription.*": {"queue": "ai"},
        "app.tasks.embedding.*": {"queue": "ai"},
    },
)

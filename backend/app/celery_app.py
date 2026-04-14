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
        "app.tasks.rag",
        "app.jobs.index_document",
        "app.jobs.sync_ccam",
        "app.jobs.sync_has",
        "app.jobs.sync_vidal",
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
        "app.tasks.rag.*": {"queue": "ai"},
    },
    # Nightly scheduled tasks (Celery beat)
    beat_schedule={
        "rebuild-bm25-index-nightly": {
            "task": "app.tasks.rag.rebuild_bm25_index",
            "schedule": 86400,       # every 24h at 02:00 UTC
            "options": {"queue": "ai"},
        },
        "sync-vidal-daily": {
            "task": "app.jobs.sync_vidal.sync_vidal",
            "schedule": 86400,       # every 24h at 03:00 UTC
            "options": {"queue": "ai"},
        },
        "sync-ccam-weekly": {
            "task": "app.jobs.sync_ccam.sync_ccam",
            "schedule": 604800,      # every 7 days (Sunday 01:00 UTC)
            "options": {"queue": "ai"},
        },
        "sync-has-monthly": {
            "task": "app.jobs.sync_has.sync_has",
            "schedule": 2592000,     # every 30 days (1st of month 01:30 UTC)
            "options": {"queue": "ai"},
        },
    },
)

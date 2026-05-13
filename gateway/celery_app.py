from __future__ import annotations

from celery import Celery
from kombu import Queue

from testagent.config.settings import get_settings


# Queue concurrency mapping (V1.0):
#   planning : concurrency=1  — Planner Agent (serial)
#   execution: concurrency=10 — Executor Agent (1–10 parallel)
#   analysis : concurrency=1  — Analyzer Agent (serial)
_QUEUE_CONCURRENCY: dict[str, int] = {
    "planning": 1,
    "execution": 10,
    "analysis": 1,
}


def create_celery_app() -> Celery:
    settings = get_settings()

    app = Celery("testagent")

    app.conf.broker_url = settings.celery_broker_url
    app.conf.result_backend = settings.celery_result_backend

    app.conf.task_serializer = "json"
    app.conf.result_serializer = "json"
    app.conf.accept_content = ["json"]

    app.conf.task_track_started = True
    app.conf.task_acks_late = True
    app.conf.worker_prefetch_multiplier = 1

    app.conf.worker_concurrency = 10

    app.conf.task_default_queue = "execution"
    app.conf.task_default_exchange = "testagent"
    app.conf.task_default_exchange_type = "topic"
    app.conf.task_default_routing_key = "execution.default"

    app.conf.task_queues = (
        Queue(
            "planning",
            routing_key="planning.#",
            queue_arguments={
                "x-max-priority": 10,
            },
        ),
        Queue(
            "execution",
            routing_key="execution.#",
            queue_arguments={
                "x-max-priority": 10,
            },
        ),
        Queue(
            "analysis",
            routing_key="analysis.#",
            queue_arguments={
                "x-max-priority": 10,
            },
        ),
    )

    app.conf.task_soft_time_limit = 300
    app.conf.task_time_limit = 330
    app.conf.task_ignore_result = False
    app.conf.task_store_errors_even_if_ignored = True

    app.conf.task_queue_max_priority = 10

    return app


celery_app = create_celery_app()

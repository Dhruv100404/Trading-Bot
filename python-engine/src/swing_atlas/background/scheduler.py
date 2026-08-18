"""APScheduler setup for the two background refresh loops.

A deliberate improvement over the Rust originals: today's `tokio::spawn` loops
die silently forever if the loop body ever panics (not just returns Err, which
is already caught and logged inline) -- there's no restart until the whole
container restarts. APScheduler's interval jobs keep firing on schedule
regardless of whether a previous run raised, and EVENT_JOB_ERROR gives a
last-resort log hook on top of each job's own try/except.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


def _on_job_error(event: JobExecutionEvent) -> None:
    logger.error("[SCHEDULER] job %s raised: %s", event.job_id, event.exception)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    return scheduler


def schedule_interval_job(
    scheduler: AsyncIOScheduler, job_id: str, func: Any, interval_secs: int, *args: Any
) -> None:
    """Adds a job that fires immediately, then every interval_secs -- matches the
    Rust originals' `loop { refresh(); sleep(interval) }` (refresh runs before the
    first sleep, not after)."""
    scheduler.add_job(
        func,
        "interval",
        seconds=interval_secs,
        args=args,
        id=job_id,
        next_run_time=datetime.now(),
    )

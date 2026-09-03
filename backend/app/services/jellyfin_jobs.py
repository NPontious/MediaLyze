from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    JellyfinConnection,
    JellyfinSyncJob,
    JellyfinSyncTriggerSource,
    JobStatus,
)
from backend.app.services.jellyfin_staging import cleanup_all_staging
from backend.app.utils.time import utc_now


ACTIVE_JELLYFIN_JOB_STATUSES = (JobStatus.queued, JobStatus.running)


def get_active_jellyfin_sync_job(db: Session) -> JellyfinSyncJob | None:
    return db.scalar(
        select(JellyfinSyncJob)
        .where(
            JellyfinSyncJob.active_lock == 1,
            JellyfinSyncJob.status.in_(ACTIVE_JELLYFIN_JOB_STATUSES),
        )
        .order_by(JellyfinSyncJob.id.desc())
    )


def get_latest_jellyfin_sync_job(db: Session) -> JellyfinSyncJob | None:
    return db.scalar(select(JellyfinSyncJob).order_by(JellyfinSyncJob.id.desc()).limit(1))


def create_or_get_jellyfin_sync_job(
    db: Session,
    trigger_source: JellyfinSyncTriggerSource,
) -> tuple[JellyfinSyncJob, bool]:
    active = get_active_jellyfin_sync_job(db)
    if active is not None:
        return active, False

    job = JellyfinSyncJob(
        status=JobStatus.queued,
        trigger_source=trigger_source,
        active_lock=1,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Another request or scheduler thread won the active-lock race.
        db.rollback()
        active = get_active_jellyfin_sync_job(db)
        if active is None:
            raise
        return active, False
    db.refresh(job)
    return job, True


def mark_jellyfin_sync_job_running(db: Session, job_id: int) -> JellyfinSyncJob | None:
    job = db.get(JellyfinSyncJob, job_id)
    if job is None or job.status != JobStatus.queued or job.active_lock != 1:
        return None
    job.status = JobStatus.running
    job.started_at = utc_now()
    job.heartbeat_at = job.started_at
    job.progress_phase = "starting"
    job.progress_detail = None
    job.progress_current = 0
    job.progress_total = None
    db.commit()
    db.refresh(job)
    return job


def update_jellyfin_sync_job_progress(
    db: Session,
    job_id: int,
    *,
    phase: str | None,
    detail: str | None,
    current: int,
    total: int | None,
) -> bool:
    job = db.get(JellyfinSyncJob, job_id)
    if job is None or job.status != JobStatus.running or job.active_lock != 1:
        return False
    job.heartbeat_at = utc_now()
    job.progress_phase = phase
    job.progress_detail = detail
    job.progress_current = max(0, int(current))
    job.progress_total = max(0, int(total)) if total is not None else None
    if phase:
        summary = dict(job.sync_summary or {})
        progress_metrics = dict(summary.get("progress_metrics") or {})
        progress_metrics[phase] = {
            "current": job.progress_current,
            "total": job.progress_total,
            "detail": detail,
        }
        summary["progress_metrics"] = progress_metrics
        job.sync_summary = summary
    db.commit()
    return True


def finish_jellyfin_sync_job(
    db: Session,
    job_id: int,
    status: JobStatus,
    *,
    summary: dict | None = None,
    error: str | None = None,
) -> JellyfinSyncJob | None:
    job = db.get(JellyfinSyncJob, job_id)
    if job is None:
        return None
    job.status = status
    job.active_lock = None
    job.finished_at = utc_now()
    job.heartbeat_at = job.finished_at
    job.sync_summary = summary or {}
    job.error = error[:2048] if error else None
    db.commit()
    db.refresh(job)
    return job


def cancel_queued_jellyfin_sync_job(db: Session, job_id: int) -> bool:
    job = db.get(JellyfinSyncJob, job_id)
    if job is None or job.status != JobStatus.queued or job.active_lock != 1:
        return False
    finished_at = utc_now()
    job.status = JobStatus.canceled
    job.active_lock = None
    job.finished_at = finished_at
    job.sync_summary = {"status": "canceled"}
    connection = db.get(JellyfinConnection, 1)
    if connection is not None:
        connection.last_status = "canceled"
        connection.last_error = None
        connection.last_sync_finished_at = finished_at
    db.commit()
    return True


def mark_jellyfin_sync_cancellation_requested(db: Session, job_id: int) -> bool:
    job = db.get(JellyfinSyncJob, job_id)
    if job is None or job.status != JobStatus.running or job.active_lock != 1:
        return False
    job.cancellation_requested = True
    db.commit()
    return True


def recover_orphaned_jellyfin_sync_jobs(db: Session) -> int:
    jobs = list(
        db.scalars(
            select(JellyfinSyncJob).where(
                JellyfinSyncJob.status.in_(ACTIVE_JELLYFIN_JOB_STATUSES)
            )
        )
    )
    cleanup_all_staging(db, commit=False)
    if not jobs:
        db.commit()
        return 0
    finished_at = utc_now()
    for job in jobs:
        job.status = JobStatus.canceled
        job.active_lock = None
        job.finished_at = finished_at
        job.error = "Jellyfin synchronization was interrupted by a process restart"
        job.sync_summary = {"status": "canceled", "reason": "process_restart"}
    connection = db.get(JellyfinConnection, 1)
    if connection is not None and connection.last_status == "running":
        connection.last_status = "canceled"
        connection.last_error = None
        connection.last_sync_finished_at = finished_at
    db.commit()
    return len(jobs)

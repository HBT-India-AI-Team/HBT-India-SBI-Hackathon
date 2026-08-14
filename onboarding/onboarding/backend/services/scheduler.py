"""
Phase 4: real, working background job system.

A simple asyncio periodic poller (SCHEDULER_POLL_INTERVAL_SECONDS, see
config.py) that queries ScheduledJob for rows with status=pending and
scheduled_for <= now, and dispatches each to a handler based on job_type.
Started as a background asyncio task on FastAPI app startup (see
backend/main.py's lifespan), not as a separate process -- no external job
queue infrastructure needed for the hackathon.

document_review outcome is controlled by the Phase 5 debug hook
(DocumentSubmission.debug_outcome) -- see /docs/DEBUG_HOOKS.md. This keeps
demo/test outcomes deterministic instead of random.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from backend import config
from backend.models.db import session_scope
from backend.models import models as m
from backend.services import events

logger = logging.getLogger("yono.scheduler")

IDLE_NUDGE_JOB_TYPE = "idle_nudge_check"
DOCUMENT_REVIEW_JOB_TYPE = "document_review"


def _sync_application_status_after_requirement_change(application, db):
    """Shared with requirement_graph._sync_application_status -- recomputes
    and, if changed, broadcasts the Application's derived status. Kept as
    a small local helper here (rather than importing the private one) to
    avoid a circular import at module load time; both call
    requirement_graph.compute_application_status() as the single source of
    truth for the derivation itself."""
    from backend.services import requirement_graph as rg
    new_status = rg.compute_application_status(application)
    if application.status != new_status:
        old = application.status
        application.status = new_status
        application.updated_at = datetime.utcnow()
        db.flush()
        events.emit("application_status_changed", {
            "application_id": application.id, "old_status": old, "new_status": new_status,
        })


def apply_document_rejection(doc: m.DocumentSubmission, requirement: m.Requirement, application: m.Application, db, reason: str):
    """Shared document-rejection path: used both by the debug_outcome=="reject"
    branch of the scheduled document_review job below, and by the
    synchronous document-type sanity check (doc_parser.classify_document())
    at upload time in routers/applications.py -- so both ways a document
    can get rejected go through the exact same state transition, failure
    counting, and escalation logic. Returns True if this rejection pushed
    the requirement into ESCALATED."""
    requirement.state = "REJECTED"
    doc.status = "REJECTED"
    doc.rejection_reason = reason
    requirement.failure_count = (requirement.failure_count or 0) + 1
    logger.warning(
        "[scheduler] document REJECTED: requirement_id=%s document_id=%s failure_count=%s reason=%s",
        requirement.id, doc.id, requirement.failure_count, reason,
    )

    escalated = False
    if requirement.failure_count >= (requirement.escalation_threshold or 2):
        requirement.state = "ESCALATED"
        escalated = True
        logger.warning("[scheduler] document requirement ESCALATED after repeated rejection: requirement_id=%s", requirement.id)
        item = m.ReviewItem(
            application_id=application.id,
            type="kyc_review",
            reason=f"document requirement {requirement.type} rejected {requirement.failure_count} times: {reason}",
            requirement_id=requirement.id,
            status="open",
        )
        db.add(item)
        db.flush()
        events.emit("hitl_item_added", {"item_id": item.id, "application_id": application.id, "type": item.type})

    requirement.updated_at = datetime.utcnow()
    db.flush()
    events.emit("requirement_updated", {
        "requirement_id": requirement.id, "application_id": application.id, "state": requirement.state,
    })
    _sync_application_status_after_requirement_change(application, db)
    return escalated


def _handle_document_review(job: m.ScheduledJob, db):
    """See /docs/DEBUG_HOOKS.md for the debug_outcome hook this reads.
    If debug_outcome is None, defaults to 'verify' (optimistic default so
    the plain happy-path demo doesn't need to pass anything extra)."""
    payload = job.payload or {}
    doc_id = payload.get("document_submission_id")
    requirement_id = payload.get("requirement_id")

    doc = db.query(m.DocumentSubmission).filter_by(id=doc_id).first()
    requirement = db.query(m.Requirement).filter_by(id=requirement_id).first()
    if doc is None or requirement is None:
        logger.error("[scheduler] document_review job=%s: doc or requirement not found (doc_id=%s requirement_id=%s)", job.id, doc_id, requirement_id)
        job.status = "failed"
        return

    outcome = doc.debug_outcome or "verify"
    application = db.query(m.Application).filter_by(id=requirement.application_id).first()
    logger.info(
        "[scheduler] document_review job=%s: document_id=%s requirement_id=%s application_id=%s outcome=%s",
        job.id, doc.id, requirement.id, application.id if application else None, outcome,
    )

    if outcome == "verify":
        requirement.state = "VERIFIED"
        doc.status = "VERIFIED"
        doc.verified_at = datetime.utcnow()
        requirement.updated_at = datetime.utcnow()
        db.flush()
        logger.info("[scheduler] document VERIFIED: requirement_id=%s document_id=%s", requirement.id, doc.id)
        events.emit("requirement_updated", {
            "requirement_id": requirement.id, "application_id": application.id, "state": requirement.state,
        })
        _sync_application_status_after_requirement_change(application, db)
    else:
        apply_document_rejection(doc, requirement, application, db, reason=doc.rejection_reason or f"debug_outcome=reject for document {doc.id}")

    job.status = "done"


def _channel_for_application(application: m.Application) -> str:
    """Best-effort guess at a notification channel based on channel_origin."""
    if application.channel_origin in ("telegram", "whatsapp", "web"):
        return application.channel_origin
    return "sms"


def _handle_idle_nudge_check(job: m.ScheduledJob, db):
    """Recurring job: finds Applications idle past the threshold without a
    recent nudge, logs a NotificationLog (mock_sent=True -- actual sending
    stays mocked, but the decision logic + scheduling are real). Reschedules
    itself for the next interval."""
    now = datetime.utcnow()
    idle_cutoff = now - timedelta(seconds=config.IDLE_THRESHOLD_SECONDS)
    cooldown_cutoff = now - timedelta(seconds=config.NUDGE_COOLDOWN_SECONDS)

    candidates = (
        db.query(m.Application)
        .filter(m.Application.status.in_(["IN_PROGRESS", "ACTION_NEEDED"]))
        .all()
    )
    for app in candidates:
        if not app.sessions:
            continue
        latest_session = max(app.sessions, key=lambda s: s.last_active_at)
        if latest_session.last_active_at > idle_cutoff:
            continue  # still active enough

        recent_notif = (
            db.query(m.NotificationLog)
            .filter(m.NotificationLog.application_id == app.id, m.NotificationLog.timestamp > cooldown_cutoff)
            .first()
        )
        if recent_notif is not None:
            continue  # within cooldown window

        from backend.services import requirement_graph as rg
        next_req = rg.get_next_requirement(app)
        req_label = next_req.label if next_req else "your application"
        product_name = app.product_id.replace("_", " ")
        message = f"Finish {req_label.lower() if next_req else 'the remaining steps'} to complete your {product_name} application."

        notif = m.NotificationLog(
            application_id=app.id,
            channel=_channel_for_application(app),
            message=message,
            mock_sent=True,
        )
        db.add(notif)
        db.flush()
        logger.info(
            "[scheduler] idle_nudge: sent nudge to application_id=%s channel=%s (mock_sent=True)",
            app.id, notif.channel,
        )
        events.emit("notification_logged", {
            "notification_id": notif.id, "application_id": app.id, "channel": notif.channel, "message": notif.message,
        })

    # reschedule self
    next_job = m.ScheduledJob(
        application_id=None,
        job_type=IDLE_NUDGE_JOB_TYPE,
        scheduled_for=now + timedelta(seconds=config.IDLE_NUDGE_CHECK_INTERVAL_SECONDS),
        status="pending",
        payload={},
    )
    db.add(next_job)
    job.status = "done"


_HANDLERS = {
    DOCUMENT_REVIEW_JOB_TYPE: _handle_document_review,
    IDLE_NUDGE_JOB_TYPE: _handle_idle_nudge_check,
}


def run_due_jobs_once():
    """Synchronous single pass -- also usable directly from tests/scripts."""
    with session_scope() as db:
        now = datetime.utcnow()
        due = (
            db.query(m.ScheduledJob)
            .filter(m.ScheduledJob.status == "pending", m.ScheduledJob.scheduled_for <= now)
            .all()
        )
        for job in due:
            handler = _HANDLERS.get(job.job_type)
            if handler is None:
                logger.warning("[scheduler] no handler for job_type=%s (job_id=%s)", job.job_type, job.id)
                job.status = "failed"
                continue
            try:
                handler(job, db)
            except Exception:
                logger.exception("[scheduler] job %s (%s) raised, marking failed", job.id, job.job_type)
                job.status = "failed"


def seed_idle_nudge_job_if_missing():
    """Seed the recurring idle_nudge_check job once at app startup if one
    doesn't already exist (pending) in the table."""
    with session_scope() as db:
        existing = (
            db.query(m.ScheduledJob)
            .filter(m.ScheduledJob.job_type == IDLE_NUDGE_JOB_TYPE, m.ScheduledJob.status == "pending")
            .first()
        )
        if existing is None:
            job = m.ScheduledJob(
                application_id=None,
                job_type=IDLE_NUDGE_JOB_TYPE,
                scheduled_for=datetime.utcnow() + timedelta(seconds=config.IDLE_NUDGE_CHECK_INTERVAL_SECONDS),
                status="pending",
                payload={},
            )
            db.add(job)
            logger.info("[scheduler] seeded initial idle_nudge_check job")


async def poller_loop():
    """The asyncio periodic poller -- started as a background task from
    the FastAPI lifespan event in backend/main.py."""
    logger.info("[scheduler] poller loop starting, interval=%ss", config.SCHEDULER_POLL_INTERVAL_SECONDS)
    while True:
        try:
            run_due_jobs_once()
        except Exception:
            logger.exception("[scheduler] poll pass raised")
        await asyncio.sleep(config.SCHEDULER_POLL_INTERVAL_SECONDS)

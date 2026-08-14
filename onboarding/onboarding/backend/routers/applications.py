"""
/backend/routers/applications.py -- Phase 3 core Application endpoints.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend import config
from backend.models.db import get_db
from backend.models import models as m
from backend.services import requirement_graph as rg, product_catalog, events
from backend.services.rule_based_engine import _prompt_for

router = APIRouter(prefix="/applications", tags=["applications"])
logger = logging.getLogger("yono.routers.applications")


class StartApplicationRequest(BaseModel):
    product_id: str
    channel: str = "web"
    language: str = "en"
    mobile_number: str | None = None
    source: str | None = None
    handoff_token: str | None = None


def _find_existing_user(db: DBSession, mobile_number: str | None):
    if not mobile_number:
        return None
    return db.query(m.User).filter_by(mobile_number=mobile_number).first()


def _active_application_for(db: DBSession, user_id: str, product_id: str):
    return (
        db.query(m.Application)
        .filter(
            m.Application.user_id == user_id,
            m.Application.product_id == product_id,
            m.Application.status.in_(["IN_PROGRESS", "UNDER_REVIEW", "ACTION_NEEDED"]),
        )
        .order_by(m.Application.created_at.desc())
        .first()
    )


def _approved_application_for(db: DBSession, user_id: str, product_id: str):
    return (
        db.query(m.Application)
        .filter(
            m.Application.user_id == user_id,
            m.Application.product_id == product_id,
            m.Application.status == "APPROVED",
        )
        .first()
    )


def _app_detail(app: m.Application):
    return {
        "id": app.id,
        "product_id": app.product_id,
        "status": app.get_status(),
        "channel_origin": app.channel_origin,
        "created_at": app.created_at.isoformat(),
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        "progress": app.get_progress(),
        "requirements": [
            {
                "id": r.id, "type": r.type, "label": r.label, "format_hint": r.format_hint,
                "mapped_step": r.mapped_step, "state": r.state, "failure_count": r.failure_count,
                "value": r.value,
            }
            for r in sorted(app.requirements, key=lambda r: (r.mapped_step, r.created_at))
        ],
    }


@router.post("/start")
def start_application(payload: StartApplicationRequest, db: DBSession = Depends(get_db)):
    try:
        product_catalog.get_product(payload.product_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="unknown_product_id")

    # handoff-token driven continuation (Phase 10 / Phase 9 reuse)
    if payload.handoff_token:
        from backend.services.handoff_tokens import consume_handoff_token
        result = consume_handoff_token(payload.handoff_token, db)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["error"])
        app = db.query(m.Application).filter_by(id=result["application_id"]).first()
        session = m.Session(application_id=app.id, channel=payload.channel, scope=result.get("scope"))
        db.add(session)
        db.commit()
        db.refresh(app)
        next_req = rg.get_next_requirement(app, scope=result.get("scope"))
        return {
            "resumed": True,
            "application": _app_detail(app),
            "session_id": session.id,
            "first_prompt": _prompt_for(next_req) if next_req else "Welcome back! You're all caught up.",
        }

    user = _find_existing_user(db, payload.mobile_number)
    if user is None:
        user = m.User(mobile_number=payload.mobile_number, language=payload.language)
        db.add(user)
        db.flush()

    # duplicate-user detection: same product already APPROVED -> distinct response
    approved = _approved_application_for(db, user.id, payload.product_id)
    if approved is not None:
        return {
            "duplicate_detected": True,
            "message": "Looks like you already have an account for this product.",
            "application": _app_detail(approved),
        }

    existing = _active_application_for(db, user.id, payload.product_id)
    if existing is not None:
        session = m.Session(application_id=existing.id, channel=payload.channel)
        db.add(session)
        db.commit()
        db.refresh(existing)
        next_req = rg.get_next_requirement(existing)
        return {
            "resumed": True,
            "application": _app_detail(existing),
            "session_id": session.id,
            "first_prompt": _prompt_for(next_req) if next_req else "Welcome back! You're all caught up.",
        }

    app = m.Application(user_id=user.id, product_id=payload.product_id, channel_origin=payload.channel)
    db.add(app)
    db.flush()
    rg.instantiate_requirements(app, db)
    session = m.Session(application_id=app.id, channel=payload.channel)
    db.add(session)
    db.commit()
    db.refresh(app)

    events.emit("application_status_changed", {"application_id": app.id, "old_status": None, "new_status": app.status})

    next_req = rg.get_next_requirement(app)
    return {
        "resumed": False,
        "application": _app_detail(app),
        "session_id": session.id,
        "first_prompt": _prompt_for(next_req) if next_req else "Welcome! Nothing pending right now.",
    }


@router.get("/{application_id}")
def get_application(application_id: str, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    detail = _app_detail(app)
    docs = (
        db.query(m.DocumentSubmission)
        .join(m.Requirement, m.DocumentSubmission.requirement_id == m.Requirement.id)
        .filter(m.Requirement.application_id == app.id)
        .all()
    )
    detail["documents"] = [
        {
            "id": d.id, "requirement_id": d.requirement_id, "file_ref": d.file_ref, "status": d.status,
            "extracted_fields": d.extracted_fields,
            "classification": d.classification,
            "rejection_reason": d.rejection_reason,
        }
        for d in docs
    ]
    return detail


@router.get("/by-user/{mobile}")
def list_applications_for_user(mobile: str, db: DBSession = Depends(get_db)):
    user = db.query(m.User).filter_by(mobile_number=mobile).first()
    if not user:
        return {"applications": []}
    apps = db.query(m.Application).filter_by(user_id=user.id).order_by(m.Application.created_at.desc()).all()
    return {"applications": [_app_detail(a) for a in apps]}


@router.get("/{application_id}/status")
def get_application_status(application_id: str, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    return {
        "id": app.id, "status": app.get_status(), "progress": app.get_progress(),
        "product_id": app.product_id,
    }


class ConsentRequest(BaseModel):
    purpose: str
    granted: bool = True


@router.post("/{application_id}/consent")
def post_consent(application_id: str, payload: ConsentRequest, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    record = m.ConsentRecord(application_id=app.id, purpose=payload.purpose, granted=payload.granted)
    db.add(record)

    consent_req = next((r for r in app.requirements if r.type == "consent"), None)
    if consent_req and payload.granted:
        consent_req.state = "VERIFIED"

    db.commit()
    events.emit("consent_logged", {"application_id": app.id, "purpose": payload.purpose, "granted": payload.granted})
    return {"ok": True, "consent_id": record.id}


@router.post("/{application_id}/documents")
async def upload_document(
    application_id: str,
    requirement_id: str = Form(...),
    debug_outcome: str | None = Form(None),
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    requirement = next((r for r in app.requirements if r.id == requirement_id), None)
    if not requirement:
        raise HTTPException(status_code=404, detail="requirement_not_found")
    if requirement.type != "document":
        raise HTTPException(status_code=400, detail="requirement_is_not_a_document_type")
    if debug_outcome not in (None, "verify", "reject"):
        raise HTTPException(status_code=400, detail="debug_outcome_must_be_verify_or_reject")

    contents = await file.read()
    file_id = uuid.uuid4().hex
    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    file_ref = os.path.join(config.UPLOAD_DIR, f"{file_id}{ext}")
    with open(file_ref, "wb") as f:
        f.write(contents)

    doc = m.DocumentSubmission(
        requirement_id=requirement.id, file_ref=file_ref, status="SUBMITTED",
        debug_outcome=debug_outcome,
    )
    db.add(doc)
    requirement.state = "VERIFYING"
    db.flush()

    # best-effort VLM extraction (Phase 7) -- never blocks the upload
    try:
        from backend.services.doc_parser import extract_fields
        doc.extracted_fields = extract_fields(file_ref, requirement.label)
    except Exception:
        pass

    # Document-type sanity check (Phase 7 continued) -- gates the upload
    # outcome, unlike extract_fields() above. Runs synchronously at upload
    # time (not deferred to the document_review scheduled job) so a
    # deliberate mismatch is rejected immediately instead of silently
    # entering the normal VERIFYING -> VERIFIED review pipeline.
    from backend.services.doc_parser import classify_document
    classification = classify_document(
        file_ref, requirement.label, original_filename=file.filename, debug_outcome=debug_outcome,
    )
    doc.classification = classification
    db.flush()

    if not classification.get("matches_expected", True):
        logger.warning(
            "[applications] document-type mismatch on upload: application_id=%s requirement_id=%s document_id=%s "
            "detected_type=%s confidence=%s reason=%s",
            app.id, requirement.id, doc.id,
            classification.get("detected_type"), classification.get("confidence"), classification.get("reason"),
        )
        from backend.services.scheduler import apply_document_rejection
        escalated = apply_document_rejection(
            doc, requirement, app, db,
            reason=f"document_type_mismatch: {classification.get('reason')}",
        )
        db.commit()
        return {
            "ok": False,
            "document_id": doc.id,
            "requirement_state": requirement.state,
            "rejected": True,
            "escalated": escalated,
            "classification": classification,
        }

    job = m.ScheduledJob(
        application_id=app.id,
        job_type="document_review",
        scheduled_for=datetime.utcnow() + timedelta(seconds=config.DOCUMENT_REVIEW_DELAY_SECONDS),
        status="pending",
        payload={"document_submission_id": doc.id, "requirement_id": requirement.id},
    )
    db.add(job)
    db.commit()

    events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": app.id, "state": "VERIFYING"})

    return {"ok": True, "document_id": doc.id, "requirement_state": "VERIFYING", "review_job_id": job.id}


@router.post("/{application_id}/edit/{requirement_id}")
def edit_requirement(application_id: str, requirement_id: str, value: str = Form(...), db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    result = rg.edit_verified_requirement(app, requirement_id, value, db)
    db.commit()
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "requirement_id": requirement_id, "new_state": result["requirement"].state}


@router.get("/{application_id}/notifications")
def get_notifications(application_id: str, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    notifs = db.query(m.NotificationLog).filter_by(application_id=application_id).order_by(m.NotificationLog.timestamp.desc()).all()
    return {"notifications": [
        {"id": n.id, "channel": n.channel, "message": n.message, "mock_sent": n.mock_sent, "timestamp": n.timestamp.isoformat()}
        for n in notifs
    ]}


class HandoffRequest(BaseModel):
    pass


@router.post("/{application_id}/handoff/{channel}")
def create_handoff(application_id: str, channel: str, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    from backend.services.handoff_tokens import generate_handoff_token, build_deep_link
    token = generate_handoff_token(app.id, channel, db, purpose="handoff")
    db.commit()
    link = build_deep_link(channel, token)
    return {"token": token, "channel": channel, "link": link, "expires_in_seconds": config.HANDOFF_TOKEN_TTL_SECONDS}


class SupportEscalateRequest(BaseModel):
    session_id: str | None = None
    reason: str | None = None


@router.post("/{application_id}/support/escalate")
def support_escalate(application_id: str, payload: SupportEscalateRequest, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    ticket = m.SupportTicket(application_id=app.id, session_id=payload.session_id, type="chat", status="open")
    db.add(ticket)
    db.flush()
    item = m.ReviewItem(
        application_id=app.id, type="support_request",
        reason=payload.reason or "Customer requested human support",
        status="open",
    )
    db.add(item)
    db.commit()
    events.emit("hitl_item_added", {"item_id": item.id, "application_id": app.id, "type": "support_request"})
    return {"ok": True, "ticket_id": ticket.id, "review_item_id": item.id}


class GuardianLinkRequest(BaseModel):
    mobile_number: str
    relationship: str = "parent"


@router.post("/{application_id}/guardian/link")
def create_guardian_link(application_id: str, payload: GuardianLinkRequest, db: DBSession = Depends(get_db)):
    """Phase 9: creates a GuardianInfo row and a short-lived access link
    (reusing Phase 10's handoff_tokens mechanism with purpose=guardian_link)
    that, once consumed, opens a scope='guardian' Session restricted to
    only the guardian_consent / guardian_mobile_otp Requirements."""
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")

    guardian_req = next((r for r in app.requirements if r.type == "guardian_consent"), None)
    if guardian_req is None:
        raise HTTPException(status_code=400, detail="application_does_not_require_guardian_consent")

    guardian = m.GuardianInfo(
        application_id=app.id, mobile_number=payload.mobile_number, relationship=payload.relationship,
    )
    db.add(guardian)
    db.flush()

    from backend.services.handoff_tokens import generate_handoff_token, build_deep_link
    token = generate_handoff_token(app.id, "web", db, purpose="guardian_link", extra={"guardian_info_id": guardian.id})
    guardian.session_id = None  # set once the guardian actually consumes the token and a Session is created
    db.commit()

    link = build_deep_link("web", token)

    # Phase 6 reuse: if a real Telegram/Email sender is configured, this is
    # where we'd actually notify the guardian. Not required for the token
    # to work -- it's also returned directly for manual demo/testing.
    return {
        "ok": True, "guardian_info_id": guardian.id, "token": token, "link": link,
        "expires_in_seconds": config.HANDOFF_TOKEN_TTL_SECONDS,
        "note": "Consume this token via POST /applications/start with handoff_token set to open a scope=guardian session.",
    }

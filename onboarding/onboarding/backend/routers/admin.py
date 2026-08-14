"""
/backend/routers/admin.py -- Phase 11 unified HITL/support queue, funnel
metrics, consent ledger, notifications, data-rights list, LLM status
(Phase 8), and the admin WebSocket feed (/ws/admin).

Broadcast wiring: services/events.py already exposes emit()/set_broadcaster();
every state-changing code path in requirement_graph.py, applications.py,
onboarding_llm.py and scheduler.py already calls events.emit(...). Here we
register a broadcaster that fans those events out to connected admin
WebSocket clients.
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.models.db import get_db
from backend.models import models as m
from backend.services import events, requirement_graph as rg
from backend.services.onboarding_llm import check_ollama_status

logger = logging.getLogger("yono.admin")

router = APIRouter(tags=["admin"])


# --------------------------------------------------------------------------
# WebSocket connection manager
# --------------------------------------------------------------------------
class _ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, event_type: str, payload: dict):
        message = json.dumps({"type": event_type, "payload": payload, "ts": datetime.utcnow().isoformat()})
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = _ConnectionManager()
events.set_broadcaster(manager.broadcast)


@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We don't expect inbound messages, but need to await something
            # to detect disconnects; ignore any content received.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
def _app_summary(app: m.Application):
    return {
        "id": app.id,
        "product_id": app.product_id,
        "status": app.get_status(),
        "channel_origin": app.channel_origin,
        "created_at": app.created_at.isoformat(),
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        "user_mobile": app.user.mobile_number if app.user else None,
    }


@router.get("/admin/applications")
def list_applications(
    status: str | None = None,
    channel: str | None = None,
    product_id: str | None = None,
    db: DBSession = Depends(get_db),
):
    query = db.query(m.Application)
    if channel:
        query = query.filter(m.Application.channel_origin == channel)
    if product_id:
        query = query.filter(m.Application.product_id == product_id)
    apps = query.order_by(m.Application.created_at.desc()).all()
    summaries = [_app_summary(a) for a in apps]
    if status:
        summaries = [s for s in summaries if s["status"] == status]
    return {"applications": summaries}


@router.get("/admin/applications/{application_id}")
def get_application_detail(application_id: str, db: DBSession = Depends(get_db)):
    app = db.query(m.Application).filter_by(id=application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="application_not_found")
    return {
        "application": _app_summary(app),
        "progress": app.get_progress(),
        "requirements": [
            {
                "id": r.id, "type": r.type, "label": r.label, "state": r.state,
                "failure_count": r.failure_count, "value": r.value, "mapped_step": r.mapped_step,
            }
            for r in sorted(app.requirements, key=lambda r: (r.mapped_step, r.created_at))
        ],
        "sessions": [
            {"id": s.id, "channel": s.channel, "scope": s.scope, "status": s.status,
             "started_at": s.started_at.isoformat(), "last_active_at": s.last_active_at.isoformat()}
            for s in app.sessions
        ],
        "messages": [
            {"session_id": msg.session_id, "direction": msg.direction, "content_type": msg.content_type,
             "content": msg.content_payload, "timestamp": msg.timestamp.isoformat()}
            for s in app.sessions for msg in sorted(s.messages, key=lambda x: x.timestamp)
        ],
    }


# --------------------------------------------------------------------------
# HITL / support unified queue
# --------------------------------------------------------------------------
@router.get("/admin/hitl/queue")
def hitl_queue(db: DBSession = Depends(get_db)):
    items = db.query(m.ReviewItem).filter_by(status="open").order_by(m.ReviewItem.created_at.desc()).all()
    return {"items": [
        {
            "id": i.id, "application_id": i.application_id, "type": i.type, "reason": i.reason,
            "status": i.status, "requirement_id": i.requirement_id, "created_at": i.created_at.isoformat(),
        }
        for i in items
    ]}


class ResolveHitlRequest(BaseModel):
    decision: str  # e.g. "approve" | "reject" | "close" | "answer"
    note: str | None = None
    # For type="content_research" (FinGuru gap-filling, Phase 4):
    answer_text: str | None = None
    source_url: str | None = None
    title: str | None = None


@router.post("/admin/hitl/{item_id}/resolve")
def resolve_hitl_item(item_id: str, payload: ResolveHitlRequest, db: DBSession = Depends(get_db)):
    item = db.query(m.ReviewItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="review_item_not_found")
    if item.status == "resolved":
        raise HTTPException(status_code=400, detail="already_resolved")

    item.status = "resolved"
    item.decision = payload.decision
    item.note = payload.note
    item.resolved_at = datetime.utcnow()

    if item.type == "kyc_review" and item.requirement_id:
        requirement = db.query(m.Requirement).filter_by(id=item.requirement_id).first()
        if requirement:
            application = db.query(m.Application).filter_by(id=requirement.application_id).first()
            if payload.decision == "approve":
                requirement.state = "VERIFIED"
            elif payload.decision == "reject":
                requirement.state = "REJECTED"
            requirement.updated_at = datetime.utcnow()
            db.flush()
            events.emit("requirement_updated", {
                "requirement_id": requirement.id, "application_id": application.id, "state": requirement.state,
            })
            new_status = rg.compute_application_status(application)
            if application.status != new_status:
                old = application.status
                application.status = new_status
                application.updated_at = datetime.utcnow()
                events.emit("application_status_changed", {
                    "application_id": application.id, "old_status": old, "new_status": new_status,
                })
    elif item.type == "support_request":
        tickets = db.query(m.SupportTicket).filter_by(application_id=item.application_id, status="open").all()
        for t in tickets:
            t.status = "closed"

    elif item.type == "content_research":
        # FinGuru gap-filling (Phase 4): the admin's answer becomes a new
        # FinGuruTopic, the linked ResearchRequest is marked answered, the
        # answer is appended to the user's conversation, and a NotificationLog
        # (the SAME mechanism onboarding nudges use) is created.
        req = db.query(m.ResearchRequest).filter_by(review_item_id=item.id).first()
        answer_text = payload.answer_text or payload.note or "Our team has looked into this."
        title = (payload.title or (req.question_text if req else item.reason) or "Researched answer")[:120]
        topic = m.FinGuruTopic(
            category="fin_wiki", title=title, tags=["researched"],
            summary=answer_text[:200], body=answer_text, source_url=payload.source_url,
            needs_review=True, last_verified_at=datetime.utcnow(),
        )
        db.add(topic)
        db.flush()
        if req:
            req.status = "answered"
            req.answered_at = datetime.utcnow()
            if req.conversation_id:
                db.add(m.FinGuruMessage(
                    conversation_id=req.conversation_id, direction="outbound", content_type="text",
                    content_payload={"text": answer_text, "researched": True},
                    citations=[{"topic_id": topic.id, "label": title[:60]}],
                ))
            notif = m.NotificationLog(
                application_id=None, channel="finguru",
                message=f"FinGuru found an answer to your question about: {(req.question_text or title)[:60]}",
                mock_sent=True,
            )
            db.add(notif)
            db.flush()
            events.emit("notification_logged", {"notification_id": notif.id, "application_id": None, "channel": "finguru", "message": notif.message})
        events.emit("research_answered", {"review_item_id": item.id, "research_request_id": req.id if req else None, "topic_id": topic.id})

    db.commit()
    events.emit("hitl_item_resolved", {"item_id": item.id, "application_id": item.application_id, "type": item.type, "decision": payload.decision})
    return {"ok": True, "item_id": item.id, "status": item.status}


# --------------------------------------------------------------------------
# Funnel metrics
# --------------------------------------------------------------------------
@router.get("/admin/funnel/summary")
def funnel_summary(db: DBSession = Depends(get_db)):
    apps = db.query(m.Application).all()
    total = len(apps)
    by_status = {}
    for a in apps:
        s = a.get_status()
        by_status[s] = by_status.get(s, 0) + 1

    requirement_verified_counts = {}
    for a in apps:
        for r in a.requirements:
            if r.state == "VERIFIED":
                requirement_verified_counts[r.type] = requirement_verified_counts.get(r.type, 0) + 1

    return {
        "application_started": total,
        "application_approved": by_status.get("APPROVED", 0),
        "by_status": by_status,
        "requirement_verified_counts": requirement_verified_counts,
    }


@router.get("/admin/funnel/by-channel")
def funnel_by_channel(db: DBSession = Depends(get_db)):
    apps = db.query(m.Application).all()
    by_channel = {}
    for a in apps:
        ch = a.channel_origin or "unknown"
        entry = by_channel.setdefault(ch, {"started": 0, "approved": 0, "by_status": {}})
        entry["started"] += 1
        status = a.get_status()
        entry["by_status"][status] = entry["by_status"].get(status, 0) + 1
        if status == "APPROVED":
            entry["approved"] += 1
    return {"by_channel": by_channel}


# --------------------------------------------------------------------------
# Consent ledger, notifications, data-rights
# --------------------------------------------------------------------------
@router.get("/admin/consent/ledger")
def consent_ledger(db: DBSession = Depends(get_db)):
    records = db.query(m.ConsentRecord).order_by(m.ConsentRecord.timestamp.desc()).all()
    return {"consents": [
        {"id": c.id, "application_id": c.application_id, "purpose": c.purpose, "granted": c.granted,
         "timestamp": c.timestamp.isoformat()}
        for c in records
    ]}


@router.get("/admin/notifications")
def all_notifications(db: DBSession = Depends(get_db)):
    notifs = db.query(m.NotificationLog).order_by(m.NotificationLog.timestamp.desc()).all()
    return {"notifications": [
        {"id": n.id, "application_id": n.application_id, "channel": n.channel, "message": n.message,
         "mock_sent": n.mock_sent, "timestamp": n.timestamp.isoformat()}
        for n in notifs
    ]}


@router.get("/admin/data-rights")
def list_data_rights(db: DBSession = Depends(get_db)):
    reqs = db.query(m.DataRightsRequest).order_by(m.DataRightsRequest.created_at.desc()).all()
    return {"requests": [
        {"id": r.id, "user_id": r.user_id, "request_type": r.request_type, "status": r.status,
         "created_at": r.created_at.isoformat(), "fulfilled_at": r.fulfilled_at.isoformat() if r.fulfilled_at else None}
        for r in reqs
    ]}


@router.post("/admin/data-rights/{request_id}/fulfill")
def fulfill_data_rights(request_id: str, db: DBSession = Depends(get_db)):
    req = db.query(m.DataRightsRequest).filter_by(id=request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="data_rights_request_not_found")
    req.status = "fulfilled"
    req.fulfilled_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": req.id, "status": req.status, "fulfilled_at": req.fulfilled_at.isoformat()}


# --------------------------------------------------------------------------
# LLM status (Phase 8)
# --------------------------------------------------------------------------
@router.get("/admin/llm/status")
def llm_status():
    return check_ollama_status()

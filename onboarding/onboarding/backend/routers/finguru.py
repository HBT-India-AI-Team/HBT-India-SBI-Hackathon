"""
/backend/routers/finguru.py -- FinGuru, the India-context financial Q&A
assistant.

Kept cleanly separate from the onboarding/applications routers: FinGuru is a
distinct feature that SHARES infrastructure (Ollama config, product catalog,
HITL ReviewItem queue, NotificationLog, STT pipeline) rather than being a
sub-feature of onboarding.

Phase 2: knowledge base + grounded chat engine endpoints.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend.models.db import get_db
from backend.models import models as m
from backend.services import finguru_engine, events

logger = logging.getLogger("yono.routers.finguru")

router = APIRouter(prefix="/finguru", tags=["finguru"])


@router.get("/health")
def finguru_health():
    return {"ok": True, "feature": "finguru"}


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------
class StartConversationRequest(BaseModel):
    mobile_number: str | None = None  # optional -- FinGuru works pre-onboarding


@router.post("/conversations/start")
def start_conversation(payload: StartConversationRequest | None = None, db: DBSession = Depends(get_db)):
    user_id = None
    if payload and payload.mobile_number:
        user = db.query(m.User).filter_by(mobile_number=payload.mobile_number).first()
        if user:
            user_id = user.id
    conv = m.FinGuruConversation(user_id=user_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"conversation_id": conv.id, "started_at": conv.started_at.isoformat()}


class FinGuruMessageRequest(BaseModel):
    text: str
    content_type: str = "text"


def _history_for(conv: m.FinGuruConversation):
    return [
        {"direction": msg.direction, "content": msg.content_payload}
        for msg in sorted(conv.messages, key=lambda x: x.timestamp)
    ]


def _run_message(conv: m.FinGuruConversation, text: str, content_type: str, db: DBSession):
    """Shared: persist inbound, run engine, persist outbound. Returns the
    engine's structured response dict. Used by the text endpoint (Phase 2) and
    the voice endpoint (Phase 3)."""
    inbound = m.FinGuruMessage(
        conversation_id=conv.id, direction="inbound", content_type=content_type,
        content_payload={"text": text},
    )
    db.add(inbound)
    db.flush()

    result = finguru_engine.ask(conv, text, db, history=_history_for(conv))

    # Phase 5 trending: increment each cited topic's query_count.
    for c in result.get("citations") or []:
        topic = db.query(m.FinGuruTopic).filter_by(id=c.get("topic_id")).first()
        if topic:
            topic.query_count = (topic.query_count or 0) + 1

    outbound = m.FinGuruMessage(
        conversation_id=conv.id, direction="outbound", content_type="text",
        content_payload={"text": result["answer_text"], "confidence": result["confidence"]},
        citations=result.get("citations") or None,
        follow_up_suggestions=result.get("follow_up_questions") or None,
    )
    db.add(outbound)
    db.commit()
    return result


@router.post("/conversations/{conversation_id}/message")
def post_finguru_message(conversation_id: str, payload: FinGuruMessageRequest, db: DBSession = Depends(get_db)):
    conv = db.query(m.FinGuruConversation).filter_by(id=conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    result = _run_message(conv, payload.text, payload.content_type, db)
    return {
        "conversation_id": conv.id,
        "answer_text": result["answer_text"],
        "citations": result.get("citations", []),
        "follow_up_questions": result.get("follow_up_questions", []),
        "confidence": result["confidence"],
        "retrieved_topic_ids": result.get("retrieved_topic_ids", []),
        "suggested_widget": result.get("suggested_widget"),
        "suggested_action": result.get("suggested_action"),
        "suggested_product_id": result.get("suggested_product_id"),
        "fraud_warning": result.get("fraud_warning", False),
        "fraud_bullets": result.get("fraud_bullets", []),
    }


@router.post("/conversations/{conversation_id}/voice")
async def post_finguru_voice(conversation_id: str, file: UploadFile = File(...), db: DBSession = Depends(get_db)):
    """Reuses the SAME STT pipeline as onboarding voice messages
    (services/stt.transcribe) -- transcribes the audio, then runs the transcript
    through the exact same ask() path as a text message. Not a parallel
    pipeline. (The Flutter frontend's mic recorder is a stub, but this endpoint
    is real and usable by any client that can POST audio.)"""
    conv = db.query(m.FinGuruConversation).filter_by(id=conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    import os as _os
    import tempfile
    contents = await file.read()
    tmp_path = tempfile.mktemp(suffix=_os.path.splitext(file.filename or "audio.wav")[1] or ".wav")
    with open(tmp_path, "wb") as f:
        f.write(contents)

    from backend.services.stt import transcribe
    stt_result = transcribe(tmp_path)
    transcript = stt_result["text"]

    result = _run_message(conv, transcript, "audio", db)
    return {
        "conversation_id": conv.id,
        "transcript": transcript,
        "transcript_mock": stt_result.get("_mock", True),
        "answer_text": result["answer_text"],
        "citations": result.get("citations", []),
        "follow_up_questions": result.get("follow_up_questions", []),
        "confidence": result["confidence"],
        "retrieved_topic_ids": result.get("retrieved_topic_ids", []),
        "suggested_widget": result.get("suggested_widget"),
        "suggested_action": result.get("suggested_action"),
        "suggested_product_id": result.get("suggested_product_id"),
        "fraud_warning": result.get("fraud_warning", False),
        "fraud_bullets": result.get("fraud_bullets", []),
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: DBSession = Depends(get_db)):
    conv = db.query(m.FinGuruConversation).filter_by(id=conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return {
        "conversation_id": conv.id,
        "user_id": conv.user_id,
        "started_at": conv.started_at.isoformat(),
        "last_active_at": conv.last_active_at.isoformat() if conv.last_active_at else None,
        "messages": [
            {
                "id": msg.id,
                "direction": msg.direction,
                "content_type": msg.content_type,
                "content": msg.content_payload,
                "citations": msg.citations,
                "follow_up_suggestions": msg.follow_up_suggestions,
                "timestamp": msg.timestamp.isoformat(),
            }
            for msg in sorted(conv.messages, key=lambda x: x.timestamp)
        ],
    }


# --------------------------------------------------------------------------
# Live comparison mode (Phase 7)
# --------------------------------------------------------------------------
class CompareRequest(BaseModel):
    question_text: str
    conversation_id: str | None = None


@router.post("/compare")
def compare(payload: CompareRequest, db: DBSession = Depends(get_db)):
    """Returns TWO genuinely different answers: the normal grounded ask() (with
    citations) and a second Ollama call with no grounding/no citation instruction
    -- representing what a generic ungrounded assistant would say."""
    history = []
    if payload.conversation_id:
        conv = db.query(m.FinGuruConversation).filter_by(id=payload.conversation_id).first()
        if conv:
            history = _history_for(conv)
    finguru_result = finguru_engine.ask(None, payload.question_text, db, history=history)
    generic_result = finguru_engine.ask_generic(payload.question_text)
    return {
        "finguru_answer": {
            "answer_text": finguru_result["answer_text"],
            "citations": finguru_result.get("citations", []),
            "confidence": finguru_result["confidence"],
        },
        "generic_answer": {
            "answer_text": generic_result["answer_text"],
        },
    }


# --------------------------------------------------------------------------
# Gap-filling / research queue (Phase 4)
# --------------------------------------------------------------------------
class ResearchRequestCreate(BaseModel):
    question_text: str
    conversation_id: str | None = None
    mobile_number: str | None = None


@router.post("/research-requests")
def create_research_request(payload: ResearchRequestCreate, db: DBSession = Depends(get_db)):
    """User opted in to "research this". Creates a ResearchRequest AND a
    ReviewItem(type="content_research") in the SAME unified admin HITL queue
    (reuses the existing ReviewItem table / /admin/hitl/queue -- not a parallel
    queue)."""
    user_id = None
    if payload.mobile_number:
        user = db.query(m.User).filter_by(mobile_number=payload.mobile_number).first()
        if user:
            user_id = user.id
    if user_id is None and payload.conversation_id:
        conv = db.query(m.FinGuruConversation).filter_by(id=payload.conversation_id).first()
        if conv:
            user_id = conv.user_id

    item = m.ReviewItem(
        application_id=None, type="content_research", reason=payload.question_text, status="open",
    )
    db.add(item)
    db.flush()

    req = m.ResearchRequest(
        conversation_id=payload.conversation_id, user_id=user_id,
        question_text=payload.question_text, status="queued", review_item_id=item.id,
    )
    db.add(req)
    db.commit()

    events.emit("hitl_item_added", {"item_id": item.id, "application_id": None, "type": "content_research"})
    return {"research_request_id": req.id, "status": req.status, "review_item_id": item.id}


@router.get("/conversations/{conversation_id}/research-updates")
def research_updates(conversation_id: str, db: DBSession = Depends(get_db)):
    """Answered research requests for this conversation -- drives the FinGuru
    home "your answer is ready" banner (Phase 4)."""
    reqs = (
        db.query(m.ResearchRequest)
        .filter_by(conversation_id=conversation_id, status="answered")
        .order_by(m.ResearchRequest.answered_at.desc())
        .all()
    )
    return {
        "updates": [
            {
                "research_request_id": r.id,
                "question_text": r.question_text,
                "status": r.status,
                "answered_at": r.answered_at.isoformat() if r.answered_at else None,
            }
            for r in reqs
        ]
    }


# --------------------------------------------------------------------------
# Trending + discovery (Phase 5)
# --------------------------------------------------------------------------
@router.get("/trending")
def trending(limit: int = 5, db: DBSession = Depends(get_db)):
    """Top topics by query_count. NOTE: this uses an ALL-TIME counter rather
    than a 7-day rolling window -- chosen to avoid adding time-windowing
    complexity for the hackathon (would need a per-citation event log rather
    than a simple counter column). Documented here per the Phase 5 prompt's
    "your call, note which you chose"."""
    topics = (
        db.query(m.FinGuruTopic)
        .filter(m.FinGuruTopic.query_count > 0)
        .order_by(m.FinGuruTopic.query_count.desc())
        .limit(limit)
        .all()
    )
    return {
        "trending": [
            {"id": t.id, "title": t.title, "summary": t.summary, "category": t.category, "query_count": t.query_count}
            for t in topics
        ]
    }


@router.get("/recent-questions")
def recent_questions(limit: int = 6, db: DBSession = Depends(get_db)):
    """Anonymized "what others are asking" feed -- question text only, no user
    identifiers, from recent inbound FinGuruMessage rows."""
    msgs = (
        db.query(m.FinGuruMessage)
        .filter(m.FinGuruMessage.direction == "inbound")
        .order_by(m.FinGuruMessage.timestamp.desc())
        .limit(limit * 3)  # over-fetch since we dedupe below
        .all()
    )
    seen = set()
    questions = []
    for msg in msgs:
        text = (msg.content_payload or {}).get("text", "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        questions.append(text)
        if len(questions) >= limit:
            break
    return {"questions": questions}


# --------------------------------------------------------------------------
# Government schemes explorer (Phase 6)
# --------------------------------------------------------------------------
@router.get("/schemes")
def list_schemes(category: str | None = None, db: DBSession = Depends(get_db)):
    """FinGuruTopic entries where category="govt_scheme", shaped for the
    schemes-explorer list-card UI. `category` here filters by SCHEME TYPE
    (savings/insurance/pension/housing -- a tag on the topic), distinct from
    FinGuruTopic.category which is the broader fin_wiki/product/govt_scheme split."""
    topics = db.query(m.FinGuruTopic).filter_by(category="govt_scheme").all()
    if category:
        topics = [t for t in topics if category.lower() in [str(x).lower() for x in (t.tags or [])]]
    return {
        "schemes": [
            {
                "id": t.id, "title": t.title, "summary": t.summary,
                "eligibility_tags": t.eligibility_tags, "tags": t.tags,
                "source_url": t.source_url,
            }
            for t in topics
        ]
    }


# --------------------------------------------------------------------------
# Topics (glossary tap-to-define + Learn more)
# --------------------------------------------------------------------------
@router.get("/topics/{topic_id}")
def get_topic(topic_id: str, db: DBSession = Depends(get_db)):
    t = db.query(m.FinGuruTopic).filter_by(id=topic_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="topic_not_found")
    return {
        "id": t.id,
        "category": t.category,
        "title": t.title,
        "tags": t.tags,
        "summary": t.summary,
        "body": t.body,
        "source_url": t.source_url,
        "eligibility_tags": t.eligibility_tags,
        "needs_review": t.needs_review,
        "last_verified_at": t.last_verified_at.isoformat() if t.last_verified_at else None,
    }

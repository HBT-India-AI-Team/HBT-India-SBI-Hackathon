"""
/backend/routers/sessions.py -- Phase 3 core Session endpoints, later
extended by Phase 7 (voice) and Phase 8 (LLM engine selection).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from backend import config
from backend.models.db import get_db
from backend.models import models as m
from backend.services import rule_based_engine

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get_engine_module():
    if config.ONBOARDING_ENGINE_MODE == "llm":
        from backend.services import onboarding_llm
        return onboarding_llm
    return rule_based_engine


def _history_for(session: m.Session):
    return [
        {"direction": msg.direction, "content": msg.content_payload}
        for msg in sorted(session.messages, key=lambda x: x.timestamp)
    ]


def _process_inbound_text(session: m.Session, application: m.Application, text: str, content_type: str, db: DBSession):
    inbound = m.Message(session_id=session.id, direction="inbound", content_type=content_type, content_payload={"text": text})
    db.add(inbound)
    session.last_active_at = datetime.utcnow()
    db.flush()

    engine = _get_engine_module()
    if engine is rule_based_engine:
        result = engine.handle_message(application, text, db, scope=session.scope)
    else:
        result = engine.handle_message(application, text, db, scope=session.scope, history=_history_for(session))

    outbound = m.Message(session_id=session.id, direction="outbound", content_type="text", content_payload={"text": result["reply_text"]})
    db.add(outbound)
    db.commit()
    return result


class MessageRequest(BaseModel):
    text: str
    content_type: str = "text"


@router.post("/{session_id}/message")
def post_message(session_id: str, payload: MessageRequest, db: DBSession = Depends(get_db)):
    session = db.query(m.Session).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    application = db.query(m.Application).filter_by(id=session.application_id).first()

    result = _process_inbound_text(session, application, payload.text, payload.content_type, db)
    return {
        "session_id": session.id,
        "reply_text": result["reply_text"],
        "actions_applied": result["actions_applied"],
        "progress": result["progress"],
        "application_status": application.get_status(),
    }


@router.get("/{session_id}/state")
def get_session_state(session_id: str, db: DBSession = Depends(get_db)):
    session = db.query(m.Session).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    application = db.query(m.Application).filter_by(id=session.application_id).first()
    messages = sorted(session.messages, key=lambda x: x.timestamp)
    return {
        "session_id": session.id,
        "channel": session.channel,
        "scope": session.scope,
        "status": session.status,
        "messages": [
            {"direction": msg.direction, "content_type": msg.content_type, "content": msg.content_payload, "timestamp": msg.timestamp.isoformat()}
            for msg in messages
        ],
        "application": {
            "id": application.id, "status": application.get_status(), "progress": application.get_progress(),
        },
    }


@router.post("/{session_id}/voice")
async def post_voice(session_id: str, file: UploadFile = File(...), db: DBSession = Depends(get_db)):
    """Phase 7: transcribes the audio then feeds the resulting text through
    the SAME message-handling path as text -- transcribe() itself is
    mocked (see services/stt.py) but this wiring is real."""
    session = db.query(m.Session).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    application = db.query(m.Application).filter_by(id=session.application_id).first()

    contents = await file.read()
    import tempfile
    import os as _os
    tmp_path = tempfile.mktemp(suffix=_os.path.splitext(file.filename or "audio.wav")[1] or ".wav")
    with open(tmp_path, "wb") as f:
        f.write(contents)

    from backend.services.stt import transcribe
    stt_result = transcribe(tmp_path)
    transcript = stt_result["text"]

    result = _process_inbound_text(session, application, transcript, "voice", db)

    import base64
    from backend.services.tts import synthesize
    reply_audio_bytes = synthesize(result["reply_text"], language=stt_result.get("language") or "en")
    reply_audio_base64 = base64.b64encode(reply_audio_bytes).decode("ascii") if reply_audio_bytes else None

    return {
        "session_id": session.id,
        "transcript": transcript,
        "transcript_mock": stt_result.get("_mock", True),
        "reply_text": result["reply_text"],
        "actions_applied": result["actions_applied"],
        "progress": result["progress"],
        "reply_audio_base64": reply_audio_base64,
        "reply_audio_mock": reply_audio_bytes is None,
    }


@router.post("/{session_id}/call/initiate")
def call_initiate(session_id: str, db: DBSession = Depends(get_db)):
    session = db.query(m.Session).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    # MOCK: no real telephony infra in this build -- mirrors original mock lifecycle
    session.status = "active"
    db.commit()
    return {"ok": True, "call_status": "initiated", "note": "MOCK: no real telephony integration in this build"}


def end_call_for_session(session: m.Session, db: DBSession):
    """Shared call-end logic -- used by both POST /call/end (below) and the
    live-call WS proxy (routers/calls.py) so there is exactly one
    "call ended" state transition, not two parallel concepts. Idempotent:
    safe to call even if the session is already "ended"."""
    session.status = "ended"
    db.commit()
    return {"ok": True, "call_status": "ended"}


@router.post("/{session_id}/call/end")
def call_end(session_id: str, db: DBSession = Depends(get_db)):
    session = db.query(m.Session).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    return end_call_for_session(session, db)

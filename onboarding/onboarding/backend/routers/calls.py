"""
/backend/routers/calls.py -- live voice call WebSocket proxy.

`WS /sessions/{id}/call/live` is a thin bidirectional relay between a
browser client and the real voice AI server's own `WS /call` endpoint
(documented in /reference/voice_ai_server_client/, protocol confirmed by
reading live_call.py there). We proxy rather than have the browser connect
directly to the upstream voice server for one reason: the upstream
connection needs `VOICE_SERVER_API_KEY` as a query-string token, and that
key must stay server-side (never sent to the browser) -- same reasoning
already applied elsewhere in this repo (e.g. OTP tokens, handoff tokens
never handed to a client that shouldn't hold them).

Design:
  1. On connect: accept the browser WS, resolve session_id -> Session ->
     Application (close with 4404 if not found -- reusing the same
     session-resolution pattern as the rest of routers/sessions.py).
  2. Open an OUTBOUND `websockets` connection from this backend to
     `{VOICE_SERVER_URL with https/http -> wss/ws}/call?token={VOICE_SERVER_API_KEY}`.
     If that connection fails/times out (the expected path in this dev
     sandbox -- no network route to the configured ngrok domain, see
     /docs/MOCKS.md), close the browser WS with code 4503 and a clear
     reason ("voice server unreachable") instead of hanging.
  3. Once connected, run two concurrent pump tasks (asyncio.gather):
     browser -> upstream (binary PCM16 audio frames forwarded as-is) and
     upstream -> browser (JSON text frames AND binary reply audio frames
     forwarded as-is). Either side closing/erroring tears down the other.
  4. Side effects while relaying (see _handle_upstream_json below):
       - {"type":"transcript",...}  -> persist Message(direction=inbound,
         content_type="voice_transcript")
       - {"type":"reply_text",...}  -> persist Message(direction=outbound,
         content_type="voice_reply_text")
       - {"type":"call_ended",...} (or browser disconnects first) ->
         finalize the call via sessions.end_call_for_session(), the exact
         same function POST /sessions/{id}/call/end uses, so there is one
         "call ended" state transition, not two parallel concepts.
"""
import asyncio
import json
import logging

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session as DBSession

from backend import config
from backend.models.db import SessionLocal
from backend.models import models as m
from backend.routers.sessions import end_call_for_session

logger = logging.getLogger("yono.calls")

router = APIRouter(prefix="/sessions", tags=["calls"])


def _upstream_ws_url() -> str:
    base = config.VOICE_SERVER_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/call?token={config.VOICE_SERVER_API_KEY}"


def _persist_transcript(session: m.Session, db: DBSession, text: str) -> None:
    db.add(m.Message(session_id=session.id, direction="inbound", content_type="voice_transcript", content_payload={"text": text}))
    db.commit()


def _persist_reply_text(session: m.Session, db: DBSession, text: str) -> None:
    db.add(m.Message(session_id=session.id, direction="outbound", content_type="voice_reply_text", content_payload={"text": text}))
    db.commit()


async def _pump_browser_to_upstream(browser_ws: WebSocket, upstream_ws) -> None:
    """Client mic audio (binary frames) -> upstream voice server, as-is."""
    try:
        while True:
            message = await browser_ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await upstream_ws.send(message["bytes"])
            elif message.get("text") is not None:
                await upstream_ws.send(message["text"])
    except WebSocketDisconnect:
        logger.info("[calls] browser disconnected (browser->upstream pump)")
    except (websockets.exceptions.ConnectionClosed, RuntimeError):
        pass
    except Exception as e:
        logger.info("[calls] browser->upstream pump ended: %s: %s", type(e).__name__, e)


async def _pump_upstream_to_browser(upstream_ws, browser_ws: WebSocket, session: m.Session, db: DBSession) -> bool:
    """Upstream JSON control frames + binary reply audio -> browser, as-is,
    with side-effect persistence for transcript/reply_text/call_ended.
    Returns True if the upstream reported call_ended (so the caller knows
    the call finished normally rather than via disconnect/error)."""
    call_ended = False
    try:
        async for message in upstream_ws:
            if isinstance(message, (bytes, bytearray)):
                await browser_ws.send_bytes(message)
                continue

            try:
                payload = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                payload = None

            if isinstance(payload, dict):
                mtype = payload.get("type")
                try:
                    if mtype == "transcript" and payload.get("text") is not None:
                        _persist_transcript(session, db, payload["text"])
                    elif mtype == "reply_text" and payload.get("text") is not None:
                        _persist_reply_text(session, db, payload["text"])
                    elif mtype == "call_ended":
                        end_call_for_session(session, db)
                        call_ended = True
                except Exception as e:
                    logger.info("[calls] side-effect persistence failed for type=%s: %s: %s", mtype, type(e).__name__, e)

            await browser_ws.send_text(message)
    except (websockets.exceptions.ConnectionClosed, RuntimeError):
        pass
    except Exception as e:
        logger.info("[calls] upstream->browser pump ended: %s: %s", type(e).__name__, e)
    return call_ended


@router.websocket("/{session_id}/call/live")
async def call_live(websocket: WebSocket, session_id: str):
    await websocket.accept()

    db = SessionLocal()
    try:
        session = db.query(m.Session).filter_by(id=session_id).first()
        if not session:
            logger.info("[calls] session_id=%s not found -- closing browser WS", session_id)
            await websocket.close(code=4404, reason="session_not_found")
            return

        application = db.query(m.Application).filter_by(id=session.application_id).first()
        if not application:
            logger.info("[calls] session_id=%s has no resolvable application -- closing browser WS", session_id)
            await websocket.close(code=4404, reason="application_not_found")
            return

        if not config.VOICE_SERVER_URL:
            logger.info("[calls] VOICE_SERVER_URL not configured -- closing browser WS as unreachable")
            await websocket.close(code=4503, reason="voice server unreachable")
            return

        upstream_url = _upstream_ws_url()
        logger.info("[calls] session_id=%s attempting upstream connect to voice server /call ...", session_id)
        try:
            upstream_ws = await websockets.connect(
                upstream_url,
                max_size=None,
                open_timeout=config.VOICE_SERVER_CONNECT_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.info(
                "[calls] session_id=%s upstream voice server unreachable (%s: %s) -- closing browser WS gracefully (expected in this sandbox)",
                session_id, type(e).__name__, e,
            )
            await websocket.close(code=4503, reason="voice server unreachable")
            return

        logger.info("[calls] session_id=%s connected to upstream voice server -- relaying", session_id)
        call_ended = False
        try:
            results = await asyncio.gather(
                _pump_browser_to_upstream(websocket, upstream_ws),
                _pump_upstream_to_browser(upstream_ws, websocket, session, db),
                return_exceptions=True,
            )
            if isinstance(results[1], bool):
                call_ended = results[1]
        finally:
            try:
                await upstream_ws.close()
            except Exception:
                pass
            if not call_ended:
                # Browser disconnected first (or the pump errored) without
                # an upstream call_ended frame -- still finalize the call
                # lifecycle the same way /call/end would, idempotently.
                try:
                    end_call_for_session(session, db)
                except Exception as e:
                    logger.info("[calls] session_id=%s failed to finalize call on teardown: %s: %s", session_id, type(e).__name__, e)
            try:
                await websocket.close()
            except Exception:
                pass
        logger.info("[calls] session_id=%s call relay ended (call_ended=%s)", session_id, call_ended)
    finally:
        db.close()

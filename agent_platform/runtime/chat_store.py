"""Persists chat sessions the same way agent_platform/state/run_store.py
persists runs: one JSON file per session, written on every turn. Exists so a
multi-turn conversation survives a server restart — real risk during a live
demo — without needing a database. Not a general checkpoint/resume system,
just enough state for agent_platform/runtime/chat.py to pick a conversation
back up by session_id.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_SESSIONS_DIR = REPO_ROOT / "chat_sessions"


def new_session_id() -> str:
    return f"chat_{secrets.token_hex(6)}"


def _session_path(session_id: str) -> Path:
    return CHAT_SESSIONS_DIR / f"{session_id}.json"


def _user_index_path() -> Path:
    return CHAT_SESSIONS_DIR / "_user_index.json"


def _read_user_index() -> dict[str, str]:
    path = _user_index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_user_index(index: dict[str, str]) -> None:
    _write_json(_user_index_path(), index)


def _user_key(user_id: str, agent_id: str) -> str:
    return f"{user_id}::{agent_id}"


def _write_json(path: Path, payload: Any) -> None:
    """Write via a temp file and one atomic rename.

    write_text() truncates and then writes, so a crash or a full disk between
    the two leaves a half-written file. That matters more now than it did when
    sessions were only a demo convenience: get_session() is behind a real
    history endpoint, and a truncated file made it raise on every subsequent
    request for that user -- permanently, until someone deleted it by hand.
    os.replace is atomic, so a reader sees either the old file or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def get_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Same guard _read_user_index has always had. Its absence here was the
        # inconsistency that turned one bad write into a permanent 500.
        logger.warning("Session %s is unreadable; treating it as absent", session_id)
        return None


def get_session_for_user(user_id: str, agent_id: str) -> dict[str, Any] | None:
    index = _read_user_index()
    session_id = index.get(_user_key(user_id, agent_id))
    if not session_id:
        return None
    return get_session(session_id)


def new_session(session_id: str, agent_id: str, user_id: str | None = None) -> dict[str, Any]:
    session = {
        "session_id": session_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "messages": [],
        "evidence": {},
        "decision": None,
    }
    if user_id:
        index = _read_user_index()
        index[_user_key(user_id, agent_id)] = session_id
        _write_user_index(index)
    return session


def record_turn(*, agent_id: str, identity: str, question: str,
                answer: str) -> str | None:
    """Append one exchange to `identity`'s conversation with this agent.

    For the stateless /invoke path, whose callers send their own history and
    no session id. Without this there is simply nothing for a history endpoint
    to return: /invoke never touched this store, so every turn the app made
    was forgotten the moment it was answered.

    Recording only. The stored history is deliberately NOT fed back into the
    next /invoke reply -- that caller already sends its own `history`, and
    injecting ours on top would double the context and, worse, silently change
    what an existing integration gets. Reading is opt-in via the endpoint.

    Never raises: a failure to write a transcript must not cost the answer
    that was already produced. Returns the session id, or None if it failed.
    """
    if not identity or not agent_id:
        return None
    try:
        session = get_session_for_user(identity, agent_id)
        if session is None:
            session = new_session(new_session_id(), agent_id, user_id=identity)
        if question:
            session["messages"].append({"role": "user", "content": question})
        if answer:
            session["messages"].append({"role": "assistant", "content": answer})
        save_session(session)
        return session["session_id"]
    except Exception:                              # noqa: BLE001 - never fatal
        logger.warning("Could not record turn for %r", identity, exc_info=True)
        return None


def save_session(session: dict[str, Any]) -> None:
    _write_json(_session_path(session["session_id"]), session)
    user_id = session.get("user_id")
    agent_id = session.get("agent_id")
    if user_id and agent_id:
        index = _read_user_index()
        index[_user_key(str(user_id), str(agent_id))] = session["session_id"]
        _write_user_index(index)

"""Persists chat sessions the same way agent_platform/state/run_store.py
persists runs: one JSON file per session, written on every turn. Exists so a
multi-turn conversation survives a server restart — real risk during a live
demo — without needing a database. Not a general checkpoint/resume system,
just enough state for agent_platform/runtime/chat.py to pick a conversation
back up by session_id.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

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
    path = _user_index_path()
    path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")


def _user_key(user_id: str, agent_id: str) -> str:
    return f"{user_id}::{agent_id}"


def get_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def save_session(session: dict[str, Any]) -> None:
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session["session_id"]).write_text(
        json.dumps(session, indent=2, default=str), encoding="utf-8",
    )
    user_id = session.get("user_id")
    agent_id = session.get("agent_id")
    if user_id and agent_id:
        index = _read_user_index()
        index[_user_key(str(user_id), str(agent_id))] = session["session_id"]
        _write_user_index(index)

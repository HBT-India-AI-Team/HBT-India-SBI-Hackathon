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


def get_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def new_session(session_id: str, agent_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "agent_id": agent_id, "messages": [], "evidence": {}, "decision": None}


def save_session(session: dict[str, Any]) -> None:
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session["session_id"]).write_text(
        json.dumps(session, indent=2, default=str), encoding="utf-8",
    )

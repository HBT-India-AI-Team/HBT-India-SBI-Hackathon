"""Per-agent API keys for the public /agents/{agent_id}/invoke endpoint.

Demo-grade: a plain JSON file mapping agent_id -> key, checked with a
constant-time comparison on invoke. No rotation policy, no scoping beyond
one key per agent, no rate limiting — deliberately minimal for a hackathon
demo of "create an agent, get a key, call it from another site." Replace
with real per-client auth before any non-demo deployment.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path


def _keys_path() -> Path:
    # Imported lazily (not at module load) so this follows wherever
    # AGENTS_DIR currently points — including test fixtures that
    # monkeypatch agent_platform.composition.loader.AGENTS_DIR to a tmp
    # dir, so tests never write into the real repo's key store.
    from agent_platform.composition.loader import AGENTS_DIR

    return AGENTS_DIR.parent / "agent_api_keys.json"


def _load() -> dict[str, str]:
    path = _keys_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(keys: dict[str, str]) -> None:
    _keys_path().write_text(json.dumps(keys, indent=2, sort_keys=True), encoding="utf-8")


def get_key(agent_id: str) -> str | None:
    return _load().get(agent_id)


def get_or_create_key(agent_id: str) -> str:
    keys = _load()
    if agent_id not in keys:
        keys[agent_id] = secrets.token_urlsafe(24)
        _save(keys)
    return keys[agent_id]


def regenerate_key(agent_id: str) -> str:
    keys = _load()
    keys[agent_id] = secrets.token_urlsafe(24)
    _save(keys)
    return keys[agent_id]


def delete_key(agent_id: str) -> None:
    keys = _load()
    if keys.pop(agent_id, None) is not None:
        _save(keys)


def is_valid(agent_id: str, candidate: str | None) -> bool:
    expected = get_key(agent_id)
    return expected is not None and candidate is not None and secrets.compare_digest(expected, candidate)

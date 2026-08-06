"""Mock lead-profile lookup, backed by a JSON fixture.

Stands in for whatever enterprise CRM/lead system a real deployment would
call. Swappable later for an MCP tool call under the same name
("lead_data.get_lead") without touching any stage code.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "leads.json"
_LEADS = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def get_lead(lead_id: str) -> dict:
    if lead_id not in _LEADS:
        raise ValueError(f"Unknown lead_id '{lead_id}'")
    # Deep-copy: callers must be free to treat this as theirs to mutate
    # without corrupting the shared fixture cache for later lookups.
    return copy.deepcopy(_LEADS[lead_id])

"""Mock credit bureau pull, backed by a JSON fixture. Stands in for a real
bureau API (CIBIL/Experian/etc.) integration.
"""
from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bureau.json"
_BUREAU = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def get_bureau_report(lead_id: str) -> dict:
    if lead_id not in _BUREAU:
        raise ValueError(f"No bureau record for lead_id '{lead_id}'")
    return _BUREAU[lead_id]

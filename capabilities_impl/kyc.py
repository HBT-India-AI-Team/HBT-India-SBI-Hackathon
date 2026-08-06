"""Mock KYC/KYB status lookup, backed by a JSON fixture. Stands in for a
real KYC/KYB and sanctions-screening system integration.
"""
from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kyc.json"
_KYC = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def get_kyc_status(lead_id: str) -> dict:
    if lead_id not in _KYC:
        raise ValueError(f"No KYC record for lead_id '{lead_id}'")
    return _KYC[lead_id]

"""Mock lead-discovery search, backed by the same JSON fixture lead_data.py
uses. Stands in for whatever real prospecting feed (GST portal, GeM, MCA,
internal CRM segments) a real deployment would query. Filtering is the
"unavoidable minimal agent-specific code" for Lead Discovery; ranking is
done separately by the generic rules engine, not here.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "leads.json"
_LEADS = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def search_leads(
    industry: str | None = None,
    location: str | None = None,
    business_need: str | None = None,
    minimum_turnover: float | None = None,
    active_only: bool = True,
) -> list[dict]:
    """minimum_turnover is in absolute currency units (e.g. 20000000 for
    2 crore); fixture records store annual_turnover_cr in crores.
    """
    results = []
    for record in _LEADS.values():
        if industry and record.get("industry") != industry:
            continue
        if location and record.get("location") != location:
            continue
        if business_need and record.get("business_need") != business_need:
            continue
        if active_only and not record.get("active", True):
            continue
        if minimum_turnover is not None:
            turnover_absolute = record.get("financials", {}).get("annual_turnover_cr", 0) * 1e7
            if turnover_absolute < minimum_turnover:
                continue
        results.append(copy.deepcopy(record))
    return results

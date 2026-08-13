"""Real (not mocked) aggregation over the same lead fixture data
lead_search.py serves — backing the demo scheduled-report trigger agent's
tool call. Every number here is genuinely computed from the fixture
records returned by a real filter, not invented by an LLM.
"""
from __future__ import annotations

from . import lead_search


def lead_pipeline_digest(location: str | None = None, industry: str | None = None) -> dict:
    leads = lead_search.search_leads(location=location, industry=industry)
    lead_count = len(leads)

    total_requested_amount_cr = round(sum(l.get("requested_amount_cr", 0) for l in leads), 2)
    average_turnover_cr = (
        round(sum(l.get("financials", {}).get("annual_turnover_cr", 0) for l in leads) / lead_count, 2)
        if lead_count else 0
    )

    business_need_breakdown: dict[str, int] = {}
    for lead in leads:
        need = lead.get("business_need", "unknown")
        business_need_breakdown[need] = business_need_breakdown.get(need, 0) + 1

    leads_by_amount = sorted(leads, key=lambda l: l.get("requested_amount_cr", 0), reverse=True)

    return {
        "location": location,
        "industry": industry,
        "lead_count": lead_count,
        "total_requested_amount_cr": total_requested_amount_cr,
        "average_turnover_cr": average_turnover_cr,
        "business_need_breakdown": business_need_breakdown,
        "business_names": [l["business_name"] for l in leads],
        # Real per-lead detail, largest request first, so a caller can ground a
        # "here's the one to watch" callout instead of guessing which name matters.
        "leads_by_requested_amount": [
            {
                "business_name": l["business_name"],
                "requested_amount_cr": l.get("requested_amount_cr", 0),
                "business_need": l.get("business_need", "unknown"),
            }
            for l in leads_by_amount
        ],
    }

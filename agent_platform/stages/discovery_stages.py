"""Lead Discovery's two agent-specific stages. Everything else in its
pipeline (load_input, reason_llm, validate_output, explain) is reused
unmodified from pipeline_stages.py. Ranking reuses the generic
rules_engine.score_category — no bespoke scoring math here, just the
search/filter step, which genuinely is discovery-specific.
"""
from __future__ import annotations

from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.runtime.pipeline import register_stage
from agent_platform.skills import rules_engine

_SIGNAL_LABELS = {
    "TURNOVER_GROWTH": "turnover growth",
    "VINTAGE": "established track record",
    "GST_ACTIVE": "active GST filing",
    "CASH_HEALTH": "healthy cash flow",
}
_SIGNAL_THRESHOLD = 70  # band_score at/above this counts as a notable signal


@register_stage("search_leads")
def search_leads(ctx, bundle, logger) -> None:
    criteria = {
        "industry": ctx.raw_input.get("industry"),
        "location": ctx.raw_input.get("location"),
        "business_need": ctx.raw_input.get("business_need"),
        "minimum_turnover": ctx.raw_input.get("minimum_turnover"),
    }
    candidates = DEFAULT_REGISTRY.invoke(
        "lead_discovery.search_leads",
        active_only=True,
        **criteria,
    )
    ctx.evidence = {"criteria": criteria, "candidates": candidates}
    logger.event(ctx, "lead_candidates_loaded", count=len(candidates), criteria=criteria)


@register_stage("rank_leads")
def rank_leads(ctx, bundle, logger) -> None:
    ranking_config = bundle.active_skills(ctx)[0].rules["ranking"]
    limit = ctx.raw_input.get("limit", 3)

    scored = []
    for candidate in ctx.evidence["candidates"]:
        score_result = rules_engine.score_category(candidate, ranking_config)
        signals = [
            _SIGNAL_LABELS[f["id"]]
            for f in score_result["factors"]
            if f["band_score"] >= _SIGNAL_THRESHOLD and f["id"] in _SIGNAL_LABELS
        ]
        scored.append({
            "id": candidate["lead_id"],
            "lead_id": candidate["lead_id"],
            "company_name": candidate["business_name"],
            "discovery_score": score_result["value"],
            "signals": signals,
            "factors": score_result["factors"],
        })

    scored.sort(key=lambda c: c["discovery_score"], reverse=True)
    ranked_leads = scored[:limit]
    selected_lead_id = ranked_leads[0]["lead_id"] if ranked_leads else None

    ctx.rule_results = {
        "ranked_leads": ranked_leads,
        "selected_lead_id": selected_lead_id,
        "candidate_count": len(ctx.evidence["candidates"]),
    }
    ctx.decision = {
        "selected_lead_id": selected_lead_id,
        "candidate_count": len(ctx.evidence["candidates"]),
        "ranked_count": len(ranked_leads),
    }
    logger.event(ctx, "lead_ranking_completed", ranked_count=len(ranked_leads))
    logger.event(ctx, "lead_selected", selected_lead_id=selected_lead_id)

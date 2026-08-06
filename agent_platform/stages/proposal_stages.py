"""Proposal's two agent-specific stages. reason_llm/validate_output/explain
are reused unmodified from pipeline_stages.py. Product ranking reuses the
generic rules_engine.evaluate_products — the only genuinely new logic here
is scoping the catalog to what qualification already approved, which is
Proposal's whole reason for existing (it must never re-derive eligibility).
"""
from __future__ import annotations

from agent_platform.runtime.pipeline import register_stage
from agent_platform.skills import rules_engine


@register_stage("select_products")
def select_products(ctx, bundle, logger) -> None:
    lead = ctx.raw_input.get("lead", {})
    qualification_result = ctx.raw_input.get("qualification_result", {})
    eligible_ids = set(ctx.raw_input.get("eligible_products", []))

    ctx.evidence = {"lead": lead, "qualification": qualification_result}

    catalog = bundle.active_skills(ctx)[0].rules["product_fit"]
    scoped_products = [p for p in catalog.get("products", []) if p["id"] in eligible_ids]
    scoped_catalog = {"products": scoped_products}

    matches = rules_engine.evaluate_products(ctx.evidence, scoped_catalog)
    for match in matches:
        # Deterministic fit_score derived from how many ranking criteria
        # this product matched — not an arbitrary rank-based number.
        match["fit_score"] = min(100, 50 + match["matched_criteria"] * 25)

    ctx.rule_results = {
        "selected_products": matches,
        "eligible_product_ids": sorted(eligible_ids),
    }
    logger.event(ctx, "product_fit_evaluated",
                 eligible_count=len(eligible_ids), matched_count=len(matches))


@register_stage("finalize_proposal")
def finalize_proposal(ctx, bundle, logger) -> None:
    products = ctx.rule_results.get("selected_products", [])
    ctx.decision = {
        "proposal_status": "READY" if products else "NO_PRODUCT_MATCH",
        "recommended_product_ids": [p["id"] for p in products],
    }

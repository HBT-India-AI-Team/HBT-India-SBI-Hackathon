"""Builds the DecisionRecord: the single artifact that answers "why did this
agent produce this result". Generic over any agent's rule_results/decision
shape — it just reads whatever the pipeline stages put on RunContext, so a
future agent with a different rules structure still gets an explanation for
free as long as its stages populate the same RunContext fields.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def build(ctx, bundle) -> dict[str, Any]:
    governing_id = ctx.governing_skill_id
    governing_results = (ctx.rule_results or {}).get(governing_id, {}) if governing_id else {}
    governing_skill = bundle.skills.get(governing_id) if governing_id else None

    record = {
        "run_id": ctx.run_id,
        "agent_id": ctx.agent_id,
        "agent_version": bundle.definition.version,
        "skill_id": governing_skill.skill_id if governing_skill else None,
        "skill_version": governing_skill.version if governing_skill else None,
        "model": bundle.definition.llm.model,
        "generated_at": _now_iso(),
        "input_summary": ctx.input_summary(),
        "gates": governing_results.get("gates", {}).get("gates", []),
        "scores": governing_results.get("scores", {}),
        "composite_score": governing_results.get("composite", {}),
        "product_recommendations": governing_results.get("products", []),
        "rule_results": ctx.rule_results,  # every active rule-bearing skill's own results, keyed by skill_id
        "decision": ctx.decision,
        "hitl": ctx.hitl,
        "llm_rationale": ctx.validated_output,
        "error": ctx.error,
    }

    record["skills_loaded"] = list(ctx.active_skill_ids)
    if ctx.active_skill_reasoning:
        record["skill_loading_reasoning"] = ctx.active_skill_reasoning
    if ctx.decision and ctx.decision.get("skill_breakdown"):
        # Every applicable skill's own outcome, not just the governing one — nothing a loaded
        # skill computed is silently discarded, even when it didn't end up governing the decision.
        record["skill_breakdown"] = ctx.decision["skill_breakdown"]

    return record


def render_markdown(record: dict[str, Any]) -> str:
    lines: list[str] = []
    decision = record.get("decision") or {}
    input_summary = record.get("input_summary") or {}
    subject = input_summary.get("business_name") or input_summary.get("lead_id") or record["run_id"]
    lines.append(f"# {record['agent_id']} — {subject}")
    lines.append("")
    if decision:
        if {"outcome", "reason", "composite_score"} & decision.keys():
            lines.append(f"**Outcome:** {decision.get('outcome', 'UNKNOWN')}")
            lines.append(f"**Reason:** {decision.get('reason', '-')}")
            lines.append(f"**Composite score:** {decision.get('composite_score', '-')}")
        else:
            # Generic fallback for agents whose decision doesn't fit the
            # qualification shape (e.g. proposal_status, selected_lead_id).
            for key, value in decision.items():
                lines.append(f"**{key.replace('_', ' ').capitalize()}:** {value}")

    hitl = record.get("hitl") or {}
    if hitl.get("triggered"):
        lines.append(f"**Human review required:** {'; '.join(hitl.get('reasons', []))}")
    lines.append("")

    skill_breakdown = record.get("skill_breakdown") or []
    if len(skill_breakdown) > 1:
        lines.append("## Skills consulted")
        for entry in skill_breakdown:
            governed = " (governs this decision)" if entry["skill_id"] == record.get("skill_id") else ""
            lines.append(
                f"- **{entry['skill_id']}**{governed}: {entry['outcome']} "
                f"(score {entry.get('composite_score', '-')}) — {entry['reason']}"
            )
        lines.append("")

    lines.append("## Eligibility gates")
    for gate in record.get("gates", []):
        mark = "PASS" if gate["passed"] else "FAIL"
        lines.append(f"- [{mark}] {gate['id']}: {gate['description']} "
                      f"(actual={gate['actual']!r})")
    lines.append("")

    lines.append("## Category scores")
    for category, result in (record.get("scores") or {}).items():
        lines.append(f"- **{category}**: {result['value']}")
        for factor in result.get("factors", []):
            lines.append(f"  - {factor['id']}: actual={factor['actual']!r} "
                          f"-> band_score={factor['band_score']}, "
                          f"weight={factor['weight']}, contribution={factor['contribution']}")
    lines.append("")

    products = record.get("product_recommendations") or []
    if products:
        lines.append("## Recommended products")
        rationale = (record.get("llm_rationale") or {}).get("product_rationale", {})
        for product in products:
            note = rationale.get(product["id"], product["reason"])
            lines.append(f"- **{product['name']}**: {note}")
        lines.append("")

    # Fallback for agents whose rule_results don't fit the
    # gates/scores/products shape above (e.g. discovery rankings, proposal
    # product-fit) — nothing computed is ever silently dropped.
    rule_results = record.get("rule_results") or {}
    already_rendered = bool(record.get("gates")) or bool(record.get("scores")) or bool(products)
    if rule_results and not already_rendered:
        lines.append("## Computed facts")
        lines.append("```json")
        lines.append(json.dumps(rule_results, indent=2, default=str))
        lines.append("```")
        lines.append("")

    rationale = record.get("llm_rationale") or {}
    if rationale:
        lines.append("## Rationale")
        lines.append(
            rationale.get("summary")
            or rationale.get("selection_reason")
            or rationale.get("customer_proposal", "")
        )
        if rationale.get("degraded"):
            lines.append("_(deterministic fallback — LLM rationale unavailable)_")
        if rationale.get("strengths"):
            lines.append("\n**Strengths**")
            for s in rationale["strengths"]:
                lines.append(f"- {s['point']} _(evidence: {s['evidence_key']})_")
        if rationale.get("risks"):
            lines.append("\n**Risks**")
            for r in rationale["risks"]:
                lines.append(f"- {r['point']} _(evidence: {r['evidence_key']})_")
        if rationale.get("next_best_action"):
            lines.append(f"\n**Next best action:** {rationale['next_best_action']}")
        lines.append(f"\n**Confidence:** {rationale.get('confidence', '-')}")
    lines.append("")

    if record.get("error"):
        lines.append("## Error")
        lines.append(f"Stage `{record['error']['stage']}` failed: "
                      f"{record['error']['type']}: {record['error']['message']}")
        lines.append("")

    lines.append("---")
    skill_label = f"{record['skill_id']}@{record['skill_version']}" if record.get("skill_id") else "-"
    lines.append(f"agent={record['agent_id']}@{record['agent_version']} "
                  f"skill={skill_label} "
                  f"model={record['model']} run_id={record['run_id']}")
    return "\n".join(lines)

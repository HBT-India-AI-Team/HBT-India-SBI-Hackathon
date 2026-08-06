"""Validates and sanitizes the LLM's structured output before it is allowed
into a run's explanation.

Two independent guarantees, both enforced here rather than trusted from the
model:
  1. The LLM cannot smuggle a decision/qualification field into its output
     and have it treated as authoritative — those keys are stripped.
  2. Every evidence citation the LLM makes must point at a real key from
     the prompt's `allowed_citation_keys`. A citation that doesn't is
     dropped from that point rather than failing the whole run, unless
     *nothing* the model said is groundable, in which case the caller
     should retry once and then fall back to a deterministic rationale.
"""
from __future__ import annotations

from typing import Any

FORBIDDEN_FIELDS = {"decision", "outcome", "qualified", "is_qualified", "score", "scores"}
REQUIRED_FIELDS = {"summary", "strengths", "risks", "next_best_action", "confidence"}


def validate_and_clean(llm_output: dict[str, Any], citation_keys: dict[str, Any],
                        required_fields: list[str] | None = None
                        ) -> tuple[bool, dict[str, Any], list[str]]:
    """Returns (needs_retry, cleaned_output, issues).

    needs_retry is True only for structural problems worth one bounded
    repair attempt: missing required fields, out-of-range confidence, or
    every single citation being ungroundable. Individually bad citations
    are dropped and noted in `issues` without forcing a retry.

    `required_fields` defaults to the lead-qualification-shaped
    REQUIRED_FIELDS for backward compatibility; pass a skill's own
    `output_contract["required"]` for any other shape — citation-grounding
    only runs on `strengths`/`risks` if those fields are actually present,
    so a skill without them (e.g. a discovery agent with just
    `selection_reason`) isn't forced into this shape.
    """
    issues: list[str] = []

    if not isinstance(llm_output, dict):
        return True, {}, ["llm output was not a JSON object"]

    for field in list(llm_output.keys()):
        if field in FORBIDDEN_FIELDS:
            issues.append(f"stripped forbidden field '{field}' from LLM output")
            llm_output.pop(field)

    required = set(required_fields) if required_fields is not None else REQUIRED_FIELDS
    missing = required - llm_output.keys()
    if missing:
        return True, llm_output, [f"missing required field(s): {sorted(missing)}"]

    if "confidence" in llm_output:
        confidence = llm_output["confidence"]
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            return True, llm_output, [f"confidence out of range or non-numeric: {confidence!r}"]

    total_points = 0
    dropped_points = 0
    for field in ("strengths", "risks"):
        if field not in llm_output:
            continue
        cleaned_items = []
        for item in llm_output.get(field) or []:
            total_points += 1
            evidence_key = item.get("evidence_key") if isinstance(item, dict) else None
            if evidence_key in citation_keys:
                cleaned_items.append(item)
            else:
                dropped_points += 1
                issues.append(f"dropped ungrounded {field} point citing '{evidence_key}'")
        llm_output[field] = cleaned_items

    if total_points > 0 and dropped_points == total_points:
        return True, llm_output, issues + ["every citation was ungrounded"]

    if "product_rationale" in llm_output:
        cleaned_rationale = {}
        for product_id, text in (llm_output.get("product_rationale") or {}).items():
            cleaned_rationale[product_id] = text
        llm_output["product_rationale"] = cleaned_rationale

    return False, llm_output, issues


def deterministic_fallback(rule_results: dict[str, Any]) -> dict[str, Any]:
    """Used when the LLM is unavailable or fails validation twice. Keeps the
    run explainable even with zero model involvement. Defensive about
    rule_results shape (`.get()` throughout) so it degrades sensibly for
    any agent's rule_results, not only lead-qualification's.
    """
    composite = rule_results.get("composite", {}).get("value")
    products = rule_results.get("products") or []
    summary = (
        f"Deterministic rationale (LLM unavailable): composite score {composite}."
        if composite is not None else "Deterministic rationale (LLM unavailable)."
    )
    return {
        "summary": summary,
        "strengths": [],
        "risks": [],
        "next_best_action": (
            f"Route to {products[0]['name']} discussion" if products
            else "Route to manual review"
        ),
        "selection_reason": summary,
        "confidence": 0.4,
        "product_rationale": {},
        "degraded": True,
    }

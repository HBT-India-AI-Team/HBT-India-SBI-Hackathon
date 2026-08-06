"""A small, generic, config-driven rules engine.

Deliberately not banking-specific: it knows how to read a dotted field path
out of an evidence dict, evaluate a condition, score a weighted factor
against banded thresholds, and rank items whose conditions match. What the
fields, weights, bands and thresholds *mean* lives entirely in the skill
package's YAML (skills_library/<skill_id>/rules/*.yaml) — a credit SME can
change qualification behaviour without touching this file.

This engine, not the LLM, is the source of truth for gates, scores and the
qualification decision. The LLM only narrates over its output (see
prompt_assembler.py / output_validator.py).
"""
from __future__ import annotations

from typing import Any

_SEVERITY = {"NOT_QUALIFIED": 2, "NEEDS_HUMAN_REVIEW": 1}

_OPERATORS = {
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
    "gte": lambda a, b: a is not None and a >= b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "lt": lambda a, b: a is not None and a < b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def get_field(evidence: dict[str, Any], path: str) -> Any:
    node: Any = evidence
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _evaluate_condition(evidence: dict[str, Any], cond: dict[str, Any]) -> bool:
    actual = get_field(evidence, cond["field"])
    operator = _OPERATORS[cond["operator"]]
    return bool(operator(actual, cond["value"]))


def evaluate_gates(evidence: dict[str, Any], gates_config: dict[str, Any]) -> dict[str, Any]:
    """Runs every declared hard gate (does not short-circuit, so the
    explanation can show every gate that was checked). Returns the most
    severe failure, if any, as `forced_decision`.
    """
    results = []
    failures = []
    for gate in gates_config.get("gates", []):
        passed = _evaluate_condition(evidence, gate)
        entry = {
            "id": gate["id"],
            "description": gate.get("description", ""),
            "field": gate["field"],
            "operator": gate["operator"],
            "value": gate["value"],
            "actual": get_field(evidence, gate["field"]),
            "passed": passed,
        }
        results.append(entry)
        if not passed:
            failures.append({
                "gate_id": gate["id"],
                "decision": gate["on_fail"]["decision"],
                "reason": gate["on_fail"]["reason"],
            })

    forced_decision = None
    if failures:
        forced_decision = max(failures, key=lambda f: _SEVERITY.get(f["decision"], 0))

    return {"gates": results, "failures": failures, "forced_decision": forced_decision}


def _score_factor(evidence: dict[str, Any], factor: dict[str, Any]) -> dict[str, Any]:
    actual = get_field(evidence, factor["field"])
    bands = sorted(factor["bands"], key=lambda b: b["min"], reverse=True)
    band_score = bands[-1]["score"] if bands else 0
    if actual is not None:
        for band in bands:
            if actual >= band["min"]:
                band_score = band["score"]
                break
    weight = factor["weight"]
    return {
        "id": factor["id"],
        "field": factor["field"],
        "actual": actual,
        "band_score": band_score,
        "weight": weight,
        "contribution": round(band_score * weight, 2),
    }


def score_category(evidence: dict[str, Any], category_config: dict[str, Any]) -> dict[str, Any]:
    factor_results = [_score_factor(evidence, f) for f in category_config.get("factors", [])]
    total_weight = sum(f["weight"] for f in category_config.get("factors", [])) or 1.0
    value = sum(fr["contribution"] for fr in factor_results) / total_weight
    return {"value": round(value, 2), "factors": factor_results}


def score_all(evidence: dict[str, Any], factors_config: dict[str, Any]) -> dict[str, Any]:
    return {
        category: score_category(evidence, cfg)
        for category, cfg in factors_config.get("categories", {}).items()
    }


def compute_composite(category_scores: dict[str, Any], composite_config: dict[str, Any]) -> dict[str, Any]:
    weights = composite_config.get("weights", {})
    total_weight = sum(weights.values()) or 1.0
    breakdown = {}
    total = 0.0
    for category, weight in weights.items():
        category_value = category_scores.get(category, {}).get("value", 0.0)
        contribution = category_value * weight
        breakdown[category] = {
            "weight": weight,
            "value": category_value,
            "contribution": round(contribution, 2),
        }
        total += contribution
    return {
        "value": round(total / total_weight, 2),
        "breakdown": breakdown,
        "thresholds": composite_config.get("thresholds", {}),
    }


def evaluate_products(evidence: dict[str, Any], product_config: dict[str, Any],
                       top_n: int = 3) -> list[dict[str, Any]]:
    matches = []
    for product in product_config.get("products", []):
        conditions = product.get("when", [])
        if all(_evaluate_condition(evidence, c) for c in conditions):
            # Pass through every field a skill's product entry declares (id,
            # name, reason, and anything extra — key_benefits,
            # required_documents, ...) rather than a fixed set, so richer
            # product catalogs don't need a different function.
            match = {k: v for k, v in product.items() if k != "when"}
            match["matched_criteria"] = len(conditions)
            matches.append(match)
    matches.sort(key=lambda m: m["matched_criteria"], reverse=True)
    return matches[:top_n]


def run_all(evidence: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Entry point the `evaluate_rules` stage calls. `rules` is
    AgentBundle.skill.rules — {"gates": {...}, "factors": {...},
    "composite": {...}, "product_fit": {...}} as loaded from YAML.
    """
    gates_result = evaluate_gates(evidence, rules["gates"])
    category_scores = score_all(evidence, rules["factors"])
    composite = compute_composite(category_scores, rules["composite"])

    forced = gates_result["forced_decision"]
    skip_products = forced is not None and forced["decision"] == "NOT_QUALIFIED"
    products = [] if skip_products else evaluate_products(evidence, rules["product_fit"])

    return {
        "gates": gates_result,
        "scores": category_scores,
        "composite": composite,
        "products": products,
    }

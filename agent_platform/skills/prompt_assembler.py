"""Turns a skill package + a run's evidence/facts into an LLM prompt.

The LLM is only ever asked to narrate over facts that already exist
(evidence values, computed facts — gate outcomes, scores, rankings,
whatever a given agent's rules produced) and to cite the specific key it's
talking about. It is never shown a blank slate and asked to invent a
decision — output_validator.py enforces that decision-bearing fields it
could try to add get stripped, and that every citation it makes points at
something real.

Generic over any evidence/facts shape: `build_citation_keys` recursively
flattens both dicts into dotted-path keys, so a new agent with an entirely
different facts shape (rankings instead of gates/scores, say) gets grounded
citations for free, with no per-agent prompt-building code.
"""
from __future__ import annotations

import json
from typing import Any

_DEFAULT_TASK = (
    "Write the qualitative narrative using ONLY the facts above. For every "
    "point in `strengths` and `risks` (if your output contract has them), "
    "set `evidence_key` to one exact string from `allowed_citation_keys`. "
    "Do not output a decision or any score — those are computed "
    "separately. Set `confidence` to your own assessment of how complete "
    "and reliable the evidence is."
)


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Recursively flattens `value` into dotted-path citation keys under
    `out`. Dicts descend by key. Lists of dicts descend by each item's `id`
    field (gates, products, ranked candidates all naturally have one);
    lists without id-bearing dict items aren't made citable — positional
    references are too easy to misquote. Leaves become citable keys.
    """
    if isinstance(value, dict):
        for key, sub_value in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _flatten(path, sub_value, out)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and "id" in item:
                item_prefix = f"{prefix}.{item['id']}"
                for key, sub_value in item.items():
                    if key == "id":
                        continue
                    _flatten(f"{item_prefix}.{key}", sub_value, out)
    else:
        out[prefix] = value


def build_citation_keys(evidence: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Flat map of every fact the LLM is allowed to cite, e.g.
    'bureau.score' -> 720, 'gates.gates.KYC_COMPLETE.passed' -> True,
    'scores.credit_risk.value' -> 82.5, 'composite.value' -> 78.0.
    """
    keys: dict[str, Any] = {}
    _flatten("", evidence, keys)
    _flatten("", facts, keys)
    keys.pop("", None)
    return keys


def build_prompt(skills: list, evidence: dict[str, Any], facts: dict[str, Any],
                  ) -> tuple[str, str, dict[str, Any]]:
    """`skills` are every Skill active for this run
    (agent_platform.composition.models.Skill instances — bundle.active_skills(ctx)).
    `facts` is whatever the pipeline's deterministic stages computed —
    typically `ctx.rule_results` (keyed by skill_id when multiple skills are
    active) — passed through as-is. No per-agent transformation needed:
    whatever shape it is, it becomes both part of the prompt payload and the
    source of groundable citation keys.

    Every active skill's own instructions are included, labeled by
    skill_id, so the model can tell which guidance came from which skill
    when more than one is active. Identical shared-guardrail text (skills
    commonly point at the same shared/compliance_guardrails.md) is only
    included once.
    """
    citation_keys = build_citation_keys(evidence, facts)

    sections: list[str] = []
    seen_shared: set[str] = set()
    for skill in skills:
        sections.append(f"--- {skill.skill_id} ---\n{skill.instructions_text}")
        if skill.shared_text and skill.shared_text not in seen_shared:
            seen_shared.add(skill.shared_text)
            sections.append(f"--- Shared platform guardrails ---\n{skill.shared_text}")
    system_prompt = "\n\n".join(sections)

    task = next((s.task_prompt_text for s in skills if s.task_prompt_text), _DEFAULT_TASK)
    payload = {
        "evidence": evidence,
        "facts": facts,
        "allowed_citation_keys": sorted(citation_keys.keys()),
        "task": task,
    }
    user_prompt = json.dumps(payload, indent=2, default=str)
    return system_prompt, user_prompt, citation_keys

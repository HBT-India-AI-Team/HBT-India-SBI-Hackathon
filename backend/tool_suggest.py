"""Which calculator, if any, this answer should open beside it.

This is the piece that was missing. Prompting the model to "call the finance
tool for EMI questions" makes it *compute* an EMI — it does not make a
calculator appear, because nothing in the reply told the client to render
one. The output contract carries `content_type: text | image` and has no way
to say "and open this widget".

So the widget is derived here, in code, from two signals:

  1. **A tool call that actually ran.** If `finance.calculate_emi` was
     invoked, the arguments it was invoked with are exactly the prefill the
     calculator wants. This is the strong signal: it cannot fire on a
     question the agent did not treat as an EMI question, and the numbers are
     the ones already quoted in the prose.

  2. **The topic, when no numbers were given.** "How does EMI work?" has
     nothing to compute, so no tool runs — but that is precisely when someone
     wants an empty calculator to play with. Keyword matched, deliberately:
     "EMI" and "FIRE" are specific enough that a keyword is reliable, and a
     model asked to set a flag will forget.

Derived rather than asked for, because this codebase's rule is not to make
the model do what code can do reliably. A model told to emit
`{"widget": "emi_calculator"}` gets it right most of the time; a mapping from
a tool call that already happened gets it right every time.
"""
from __future__ import annotations

import re
from typing import Any

from . import tool_store

# Capability -> (tool_id, {capability arg: widget input key}).
# The argument names differ between the two on purpose; see tool_store.
_FROM_CAPABILITY: dict[str, tuple[str, dict[str, str]]] = {
    "finance.calculate_emi": ("emi_calculator", {
        "principal": "principal",
        "annual_rate_percent": "rate",
        "tenure_months": "months",
    }),
    "money.sip_projection": ("fire_calculator", {
        "monthly_investment": "monthly_investment",
        "annual_return_percent": "annual_return",
        "years": "years",
    }),
    "money.sip_required_for_goal": ("fire_calculator", {
        "annual_return_percent": "annual_return",
        "years": "years",
    }),
}

# Word-boundary matched so "premium" does not trigger on "emi", which it
# would under a naive substring check — as would "semi", "chemistry" and
# every Tamil transliteration containing those letters.
_TOPICS: list[tuple[str, re.Pattern[str]]] = [
    ("emi_calculator", re.compile(
        r"\bemi\b|\bequated monthly\b|\bloan (?:payment|instal?ment|repayment)\b"
        r"|\bmonthly instal?ment\b|\bकिस्त\b|\bமாதத் தவணை\b",
        re.IGNORECASE)),
    ("fire_calculator", re.compile(
        r"\bfire\b(?!\s*(?:alarm|safety|insurance))|\bretire early\b|\bearly retirement\b"
        r"|\bfinancial independence\b|\bretirement corpus\b|\bcorpus\b",
        re.IGNORECASE)),
]


def _tool_calls(stage_trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for entry in stage_trace or []:
        detail = entry.get("detail")
        if isinstance(detail, dict):
            calls.extend(detail.get("tool_calls") or [])
    return calls


def _definition(tool_id: str) -> dict[str, Any] | None:
    try:
        return tool_store.get_tool_by_id(tool_id)
    except Exception:                           # noqa: BLE001 - never fatal
        # A calculator failing to load must not cost someone their answer.
        return None


def suggest(message: str, stage_trace: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Calculators to offer alongside this reply. Empty list is the norm.

    Ordered strongest first, and never more than one of each kind: two EMI
    calculators in one reply is noise, and a prefilled one always beats an
    empty one.
    """
    suggestions: dict[str, dict[str, Any]] = {}

    for call in _tool_calls(stage_trace):
        mapping = _FROM_CAPABILITY.get(call.get("name") or "")
        if mapping is None:
            continue
        tool_id, arg_map = mapping
        if tool_id in suggestions:
            continue
        arguments = call.get("arguments") or {}
        prefill = {
            widget_key: arguments[capability_arg]
            for capability_arg, widget_key in arg_map.items()
            if isinstance(arguments, dict) and arguments.get(capability_arg) is not None
        }
        definition = _definition(tool_id)
        if definition is None:
            continue
        suggestions[tool_id] = {
            "tool_id": tool_id,
            "reason": "computed",
            "prefill": prefill,
            "tool": definition,
        }

    if isinstance(message, str) and message.strip():
        for tool_id, pattern in _TOPICS:
            if tool_id in suggestions or not pattern.search(message):
                continue
            definition = _definition(tool_id)
            if definition is None:
                continue
            suggestions[tool_id] = {
                "tool_id": tool_id,
                # No tool ran, so there is nothing to prefill — the point here
                # is to hand someone an empty calculator to try numbers in.
                "reason": "mentioned",
                "prefill": {},
                "tool": definition,
            }

    return sorted(suggestions.values(), key=lambda s: s["reason"] != "computed")

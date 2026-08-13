"""Real (not mocked) financial calculations, plus small deterministic rate
tables — backing the demo "financial guru" chat agent's tool calls. The
rate tables are demo data (no live market feed), but every function here
actually computes/looks up its result in real code; nothing is fabricated
by an LLM.
"""
from __future__ import annotations

_FD_RATE_TABLE = [  # (min tenure in months, annual rate %) — longest matching bracket wins
    (3, 6.5),
    (6, 7.0),
    (12, 7.25),
    (24, 7.5),
    (36, 7.1),
    (60, 7.0),
]

_SAVINGS_RATE_PERCENT = 3.5


def get_fd_rate(tenure_months: int) -> dict:
    best_bracket, best_rate = _FD_RATE_TABLE[0]
    for months, rate in _FD_RATE_TABLE:
        if tenure_months >= months:
            best_bracket, best_rate = months, rate
    return {"tenure_months": tenure_months, "annual_rate_percent": best_rate, "rate_bracket_months": best_bracket}


def get_savings_rate() -> dict:
    return {"annual_rate_percent": _SAVINGS_RATE_PERCENT}


def calculate_emi(principal: float, annual_rate_percent: float, tenure_months: int) -> dict:
    """Standard reducing-balance EMI formula — a real computation, not a lookup."""
    monthly_rate = annual_rate_percent / 12 / 100
    if monthly_rate == 0:
        emi = principal / tenure_months
    else:
        factor = (1 + monthly_rate) ** tenure_months
        emi = principal * monthly_rate * factor / (factor - 1)
    total_payment = emi * tenure_months
    total_interest = total_payment - principal
    return {
        "emi": round(emi, 2),
        "total_payment": round(total_payment, 2),
        "total_interest": round(total_interest, 2),
    }

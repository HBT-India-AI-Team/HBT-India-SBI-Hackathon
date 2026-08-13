"""Deterministic personal-finance calculations for FinGuru.

Every function here is real arithmetic in Python. None of it is an estimate,
and none of it is left to the language model — a 12B model doing compound
interest in its head produces confident, plausible, wrong numbers, and a
wrong number in a finance answer is worse than no answer.

Each function validates its own inputs and returns `{"ok": False, "reason":
...}` rather than raising, so a nonsense tool call from the model comes back
as something it can explain to the user instead of crashing the run.

Conventions, stated because they change the answer:
- Rates are annual percentages (7.5 means 7.5%), never decimals.
- FDs compound quarterly, which is what Indian banks actually do.
- SIPs are treated as annuity-due (invested at the start of each month),
  matching how mutual-fund SIP calculators report returns.
"""
from __future__ import annotations

import math
from typing import Any

_MAX_MONTHS = 1200  # 100 years — beyond this the inputs are a typo, not a plan.


def _positive_number(value: Any, name: str) -> tuple[float | None, str | None]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a number, got {value!r}"
    if not math.isfinite(number):
        return None, f"{name} must be a finite number, got {value!r}"
    if number <= 0:
        return None, f"{name} must be greater than zero, got {number}"
    return number, None


def _non_negative_number(value: Any, name: str) -> tuple[float | None, str | None]:
    """Like _positive_number but allows zero — a 0% prepayment charge is a
    real and common answer (RBI bars foreclosure charges on floating-rate
    home loans to individuals), not a missing value."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a number, got {value!r}"
    if not math.isfinite(number) or number < 0:
        return None, f"{name} must be zero or a positive finite number, got {value!r}"
    return number, None


def _months(value: Any, name: str = "tenure_months") -> tuple[int | None, str | None]:
    number, error = _positive_number(value, name)
    if error:
        return None, error
    months = int(round(number))
    if months > _MAX_MONTHS:
        return None, f"{name} of {months} is unrealistically long (max {_MAX_MONTHS})"
    return months, None


def fd_maturity(principal: float, annual_rate_percent: float, tenure_months: int) -> dict:
    """Maturity value of a fixed deposit, compounded quarterly.

    Pair this with india_rates.get_fd_rate — look the rate up, then compute
    here. The rate carries its own as_of date; this function just does the
    arithmetic on whatever rate it is handed.
    """
    amount, error = _positive_number(principal, "principal")
    if error:
        return {"ok": False, "reason": error}
    rate, error = _positive_number(annual_rate_percent, "annual_rate_percent")
    if error:
        return {"ok": False, "reason": error}
    months, error = _months(tenure_months)
    if error:
        return {"ok": False, "reason": error}

    quarters = months / 3
    maturity = amount * (1 + rate / 400) ** quarters
    interest = maturity - amount
    return {
        "ok": True,
        "principal": round(amount, 2),
        "annual_rate_percent": rate,
        "tenure_months": months,
        "maturity_value": round(maturity, 2),
        "interest_earned": round(interest, 2),
        "compounding": "quarterly",
        "note": "Interest on FDs is taxable at your slab rate; this figure is before tax.",
    }


def sip_projection(monthly_investment: float, annual_return_percent: float, years: float) -> dict:
    """Projected value of a monthly SIP, as an annuity-due.

    `annual_return_percent` is an assumption the user or FinGuru supplies —
    it is NOT a rate this platform looked up anywhere, because nobody can
    look up a future equity return. The response says so explicitly so the
    projection is never presented as a promise.
    """
    contribution, error = _positive_number(monthly_investment, "monthly_investment")
    if error:
        return {"ok": False, "reason": error}
    rate, error = _positive_number(annual_return_percent, "annual_return_percent")
    if error:
        return {"ok": False, "reason": error}
    duration, error = _positive_number(years, "years")
    if error:
        return {"ok": False, "reason": error}

    n = int(round(duration * 12))
    if n > _MAX_MONTHS:
        return {"ok": False, "reason": f"{duration} years is unrealistically long"}

    monthly_rate = rate / 12 / 100
    future_value = contribution * (((1 + monthly_rate) ** n - 1) / monthly_rate) * (1 + monthly_rate)
    invested = contribution * n
    return {
        "ok": True,
        "monthly_investment": round(contribution, 2),
        "assumed_annual_return_percent": rate,
        "years": duration,
        "months": n,
        "total_invested": round(invested, 2),
        "projected_value": round(future_value, 2),
        "projected_gain": round(future_value - invested, 2),
        "is_projection_not_guarantee": True,
        "note": (
            "The return rate is an assumption, not a looked-up or promised figure. Actual market "
            "returns vary year to year and can be negative."
        ),
    }


def sip_required_for_goal(target_amount: float, annual_return_percent: float, years: float) -> dict:
    """The monthly SIP needed to reach a target — the inverse of
    sip_projection, so goal questions don't get answered by trial and error.
    """
    target, error = _positive_number(target_amount, "target_amount")
    if error:
        return {"ok": False, "reason": error}
    rate, error = _positive_number(annual_return_percent, "annual_return_percent")
    if error:
        return {"ok": False, "reason": error}
    duration, error = _positive_number(years, "years")
    if error:
        return {"ok": False, "reason": error}

    n = int(round(duration * 12))
    if n > _MAX_MONTHS:
        return {"ok": False, "reason": f"{duration} years is unrealistically long"}

    monthly_rate = rate / 12 / 100
    contribution = target * monthly_rate / (((1 + monthly_rate) ** n - 1) * (1 + monthly_rate))
    return {
        "ok": True,
        "target_amount": round(target, 2),
        "assumed_annual_return_percent": rate,
        "years": duration,
        "required_monthly_investment": round(contribution, 2),
        "total_invested": round(contribution * n, 2),
        "is_projection_not_guarantee": True,
        "note": "Assumes the return rate holds on average across the whole period.",
    }


# GST on a lender's fee-based service. Loan *interest* is exempt, but the
# prepayment/foreclosure *charge* is a fee and is taxed -- which is why a
# "3% charge" actually costs 3.54% of the amount prepaid. Quoting the charge
# without it understates what the customer pays at the counter.
GST_ON_FEES_PERCENT = 18.0


def prepayment_savings(balance: float, annual_rate_percent: float, monthly_payment: float,
                        prepay_amount: float, prepayment_charge_percent: float = 0.0) -> dict:
    """What a one-off lump-sum prepayment actually buys: months cut off the
    loan and interest saved, keeping the EMI the same.

    "Should I prepay?" is usually answered with a principle ("high-interest
    debt first") when the person wants a number. The number is the whole
    decision -- ₹40,000 against a 14% loan either saves enough to be worth
    losing the buffer or it doesn't, and that depends on the remaining
    tenure, which no rule of thumb knows.

    Pass `prepayment_charge_percent` from the loan record and the charge is
    netted off here, GST included. It is computed rather than left to the
    caller because the obvious mental arithmetic ("3% of 50,000 is 1,500")
    silently omits the 18% GST on that fee, and a net saving quoted 270
    rupees light is exactly the kind of small confident error this whole
    agent exists to avoid.
    """
    owed, error = _positive_number(balance, "balance")
    if error:
        return {"ok": False, "reason": error}
    rate, error = _positive_number(annual_rate_percent, "annual_rate_percent")
    if error:
        return {"ok": False, "reason": error}
    payment, error = _positive_number(monthly_payment, "monthly_payment")
    if error:
        return {"ok": False, "reason": error}
    prepay, error = _positive_number(prepay_amount, "prepay_amount")
    if error:
        return {"ok": False, "reason": error}

    if prepay >= owed:
        return {
            "ok": True,
            "clears_the_loan": True,
            "balance": round(owed, 2),
            "prepay_amount": round(prepay, 2),
            "note": (
                "The prepayment is at least the outstanding balance, so it closes the loan "
                "outright. Ask the lender for the exact foreclosure figure including any charge."
            ),
        }

    before = debt_payoff_time(owed, rate, payment)
    if not before.get("ok"):
        return before
    after = debt_payoff_time(owed - prepay, rate, payment)
    if not after.get("ok"):
        return after

    interest_saved = before["total_interest"] - after["total_interest"]

    charge_rate, error = _non_negative_number(prepayment_charge_percent, "prepayment_charge_percent")
    if error:
        return {"ok": False, "reason": error}
    charge = prepay * charge_rate / 100
    gst = charge * GST_ON_FEES_PERCENT / 100
    total_charge = charge + gst

    return {
        "ok": True,
        "clears_the_loan": False,
        "balance": round(owed, 2),
        "annual_rate_percent": rate,
        "monthly_payment": round(payment, 2),
        "prepay_amount": round(prepay, 2),
        "months_to_clear_before": before["months_to_clear"],
        "months_to_clear_after": after["months_to_clear"],
        "months_saved": before["months_to_clear"] - after["months_to_clear"],
        "total_interest_before": before["total_interest"],
        "total_interest_after": after["total_interest"],
        "interest_saved_gross": round(interest_saved, 2),
        "prepayment_charge_percent": charge_rate,
        "prepayment_charge": round(charge, 2),
        "gst_on_charge": round(gst, 2),
        "total_charge_payable": round(total_charge, 2),
        "net_saving": round(interest_saved - total_charge, 2),
        "worth_it": interest_saved > total_charge,
        "note": (
            f"net_saving is what the customer actually keeps: gross interest saved minus the "
            f"prepayment charge and {GST_ON_FEES_PERCENT}% GST on that charge. Quote net_saving, "
            "not the gross figure. Assumes the EMI stays the same and the tenure shortens — if "
            "the lender lowers the EMI instead, the saving is far smaller."
        ),
    }


def debt_payoff_time(balance: float, annual_rate_percent: float, monthly_payment: float) -> dict:
    """How long a debt takes to clear at a fixed monthly payment, and the
    total interest paid getting there.

    The important branch is the one where the payment never clears the debt:
    if the monthly payment does not exceed the first month's interest, the
    balance grows forever. That returns `ok: False` with the minimum payment
    needed, which is the genuinely useful answer to give someone.
    """
    owed, error = _positive_number(balance, "balance")
    if error:
        return {"ok": False, "reason": error}
    rate, error = _positive_number(annual_rate_percent, "annual_rate_percent")
    if error:
        return {"ok": False, "reason": error}
    payment, error = _positive_number(monthly_payment, "monthly_payment")
    if error:
        return {"ok": False, "reason": error}

    monthly_rate = rate / 12 / 100
    first_month_interest = owed * monthly_rate
    if payment <= first_month_interest:
        return {
            "ok": False,
            "reason": (
                f"A payment of {round(payment, 2)} never clears this debt — the first month's interest "
                f"alone is {round(first_month_interest, 2)}, so the balance grows."
            ),
            "minimum_payment_to_make_progress": round(first_month_interest, 2),
            "monthly_interest_at_current_balance": round(first_month_interest, 2),
        }

    months = -math.log(1 - (monthly_rate * owed) / payment) / math.log(1 + monthly_rate)
    months_rounded = int(math.ceil(months))
    total_paid = payment * months
    return {
        "ok": True,
        "balance": round(owed, 2),
        "annual_rate_percent": rate,
        "monthly_payment": round(payment, 2),
        "months_to_clear": months_rounded,
        "years_to_clear": round(months_rounded / 12, 1),
        "total_paid": round(total_paid, 2),
        "total_interest": round(total_paid - owed, 2),
    }


def budget_split(monthly_take_home: float, needs_percent: float = 50, wants_percent: float = 30,
                 savings_percent: float = 20) -> dict:
    """Split take-home pay across needs/wants/savings.

    Defaults to the 50/30/20 rule but takes explicit percentages, so FinGuru
    can reflect a split the user already follows instead of insisting on the
    textbook one. Percentages must total 100 — silently normalising them
    would hand back numbers that don't match what was asked for.
    """
    income, error = _positive_number(monthly_take_home, "monthly_take_home")
    if error:
        return {"ok": False, "reason": error}

    parts: dict[str, float] = {}
    for name, value in (("needs", needs_percent), ("wants", wants_percent), ("savings", savings_percent)):
        try:
            share = float(value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": f"{name}_percent must be a number, got {value!r}"}
        if share < 0:
            return {"ok": False, "reason": f"{name}_percent cannot be negative"}
        parts[name] = share

    total = sum(parts.values())
    if abs(total - 100) > 0.01:
        return {"ok": False, "reason": f"Percentages must add up to 100, got {round(total, 2)}"}

    return {
        "ok": True,
        "monthly_take_home": round(income, 2),
        "split": {name: round(income * share / 100, 2) for name, share in parts.items()},
        "percentages": parts,
        "note": "Based on take-home (post-tax) pay, not gross salary.",
    }


def emergency_fund_target(monthly_essential_expenses: float, months_of_cover: float = 6) -> dict:
    """Emergency-fund target and how long it takes to build at a given
    monthly saving rate (when supplied via months_of_cover alone, just the
    target).
    """
    expenses, error = _positive_number(monthly_essential_expenses, "monthly_essential_expenses")
    if error:
        return {"ok": False, "reason": error}
    cover, error = _positive_number(months_of_cover, "months_of_cover")
    if error:
        return {"ok": False, "reason": error}

    return {
        "ok": True,
        "monthly_essential_expenses": round(expenses, 2),
        "months_of_cover": cover,
        "target_amount": round(expenses * cover, 2),
        "note": (
            "Essential expenses only — rent, EMIs, food, utilities, insurance. Keep this in a "
            "liquid place (savings account or sweep-in FD), not in equity."
        ),
    }

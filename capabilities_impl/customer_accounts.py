"""Read-only access to the signed-in customer's actual banking position.

This is the capability a general-purpose assistant structurally cannot have.
Everything else FinGuru does -- rates, RBI rules, arithmetic -- a good public
model can approximate. None of them know that *this* customer owes ₹3,18,500
at 14.25% with an EMI of ₹10,480 and a 3% prepayment charge, so none of them
can answer "should I prepay?" without first asking three questions the bank
already knows the answer to.

In production these functions become read-only calls into the core banking
system. The fixture stands in for that; the shape of what's returned is the
part that matters, and it deliberately includes the things a real answer
turns on and a generic one omits -- the prepayment charge, whether a rate is
floating, the actual EMI rather than an assumed one.

Two deliberate constraints:

**Read-only.** Nothing here moves money, opens or closes anything. An agent
that can act on an account needs an authorisation model, not a prompt.

**Never returns identifiers.** No account numbers, no card numbers, no PAN.
The agent only needs balances and terms to reason; handing it identifiers
means they can end up in a model's context, in a log, and in a reply. The
`account_ref` values are opaque demo handles, not account numbers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo_customers.json"

# In production the customer identity comes from the authenticated session and
# is never guessable from chat input. For the demo the chat layer injects this
# default so a conversation has someone to be.
DEFAULT_DEMO_CUSTOMER = "SBIDEMO001"

_data: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _data
    if _data is None:
        _data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return _data


def _customer(customer_id: str | None) -> dict[str, Any] | None:
    customers = _load().get("customers", {})
    return customers.get(customer_id or DEFAULT_DEMO_CUSTOMER)


def get_profile(customer_id: str | None = None) -> dict[str, Any]:
    """The customer's whole position: deposits, loans and cards, with the
    terms each one actually carries."""
    record = _customer(customer_id)
    if record is None:
        return {
            "available": False,
            "reason": f"no customer record for {customer_id!r}",
        }

    deposits = [a for a in record["accounts"]]
    total_deposits = sum(
        a.get("balance", a.get("principal", 0)) for a in deposits
    )
    total_borrowing = sum(l["outstanding_principal"] for l in record["loans"])
    total_borrowing += sum(c["outstanding"] for c in record["cards"])

    return {
        "available": True,
        "customer_id": record["customer_id"],
        "name": record["name"],
        "home_branch": record["home_branch"],
        "monthly_credit": record["monthly_credit"],
        "monthly_credit_label": record["monthly_credit_label"],
        "deposits": deposits,
        "loans": record["loans"],
        "cards": record["cards"],
        "total_deposits": round(total_deposits, 2),
        "total_borrowing": round(total_borrowing, 2),
        "note": (
            "These are this customer's real held positions. Use these exact figures — "
            "do not ask the customer for their balance, EMI or interest rate, and do not "
            "assume a value when one is present here."
        ),
    }


def get_borrowings(customer_id: str | None = None) -> dict[str, Any]:
    """Just the debts, most expensive first.

    Ordered by rate on purpose: "which of these should I clear first" is the
    most common question this data answers, and the ordering *is* the answer.
    A credit card at 42% next to a home loan at 8.5% makes the priority
    obvious in a way that a list in account-opening order does not.
    """
    record = _customer(customer_id)
    if record is None:
        return {"available": False, "reason": f"no customer record for {customer_id!r}"}

    borrowings = []
    for loan in record["loans"]:
        borrowings.append({
            "kind": loan["type"],
            "account_ref": loan["account_ref"],
            "outstanding": loan["outstanding_principal"],
            "annual_rate_percent": loan["annual_rate_percent"],
            "emi": loan["emi"],
            "remaining_tenure_months": loan["remaining_tenure_months"],
            "prepayment_charge_percent": loan.get("prepayment_charge_percent"),
            "prepayment_note": loan.get("prepayment_note"),
            "rate_type": loan.get("rate_type"),
        })
    for card in record["cards"]:
        borrowings.append({
            "kind": card["type"],
            "account_ref": card["account_ref"],
            "outstanding": card["outstanding"],
            "annual_rate_percent": card["annual_rate_percent"],
            "emi": None,
            "minimum_due": card.get("minimum_due"),
            "remaining_tenure_months": None,
            "prepayment_charge_percent": 0.0,
        })

    borrowings.sort(key=lambda b: b["annual_rate_percent"], reverse=True)
    return {
        "available": True,
        "customer_id": record["customer_id"],
        "borrowings": borrowings,
        "total_borrowing": round(sum(b["outstanding"] for b in borrowings), 2),
        "note": (
            "Sorted most expensive first. Clearing the highest rate saves the most per "
            "rupee, but check prepayment_charge_percent before recommending it — a charge "
            "can outweigh the saving on a small or nearly-finished loan."
        ),
    }

"""SBI debit-card merchant offers, with their expiry enforced rather than stored.

Offers are a different shape of fact from everything else this agent quotes.
A savings rate drifts: quoting last quarter's is imprecise. An offer *ends*,
and quoting an ended one is simply false -- the user goes to the shop, the
discount does not apply, and the assistant sent them there.

So nothing here reports an offer as available. `active` is computed against
today's date on every call, exactly like india_rates computes `stale`, because
a flag written into the file would be wrong the morning after it was written.

Two states worth separating, and the capability returns both rather than
filtering silently:

  expired    the window closed -- say so and say when, do not quote it as live
  upcoming   the window has not opened -- "from 21 August" is a useful answer

Offers whose page publishes no dates at all (Cleartrip, Flipkart Travel, the
business bundle) come back with dates_published=False. They are not claimed to
be active, because nobody said they were; the honest line is that the bank
lists them without a period.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sbi_debit_card_offers.json"
_DATA = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _today() -> date:
    return date.today()


def _parse(value: str | None) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _window_status(valid_from: str | None, valid_to: str | None) -> dict[str, Any]:
    """Where today sits relative to one offer window."""
    start, end = _parse(valid_from), _parse(valid_to)
    if start is None and end is None:
        return {"dates_published": False, "status": "no_period_published",
                "active": False}

    today = _today()
    if end is not None and today > end:
        return {"dates_published": True, "status": "expired", "active": False,
                "ended_days_ago": (today - end).days}
    if start is not None and today < start:
        return {"dates_published": True, "status": "upcoming", "active": False,
                "starts_in_days": (start - today).days}
    return {
        "dates_published": True, "status": "active", "active": True,
        # The number that decides whether to mention it at all: an offer with
        # two days left is worth flagging as ending, not just listing.
        "days_remaining": (end - today).days if end is not None else None,
    }


def _decorate(offer: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(offer)
    out.update(_window_status(offer.get("valid_from"), offer.get("valid_to")))

    # A second window is how the page expresses a festival extension -- the
    # Onam dates for Kerala stores sit outside the main Independence Day run.
    # An offer is active if ANY of its windows is.
    extras = []
    for window in offer.get("additional_windows") or []:
        status = _window_status(window.get("valid_from"), window.get("valid_to"))
        extras.append({**window, **status})
        if status.get("active"):
            out["active"] = True
            out["status"] = "active"
    if extras:
        out["additional_windows"] = extras
    return out


def _provenance() -> dict[str, Any]:
    as_of = _parse(_DATA.get("as_of"))
    max_age = _DATA.get("max_age_days")
    age_days = (_today() - as_of).days if as_of else None
    return {
        "source_name": _DATA.get("source_name"),
        "source_url": _DATA.get("source_url"),
        "page_last_updated": _DATA.get("page_last_updated"),
        "as_of": _DATA.get("as_of"),
        "age_days": age_days,
        # Offers churn monthly, so a month-old scrape may already be missing
        # new ones -- distinct from any single offer having expired.
        "stale": age_days is None or (max_age is not None and age_days > max_age),
    }


def get_card_offers(merchant: str | None = None, category: str | None = None,
                    include_inactive: bool = False) -> dict:
    """Current SBI debit-card offers, with each one's dates checked against today.

    `merchant` matches loosely ("amazon", "big basket", "flipkart") because
    that is how someone asks. `category` filters by grocery / travel /
    pharmacy / electronics / quick_commerce / business.

    By default only offers that are live today come back. Pass
    include_inactive=True to also get expired and upcoming ones, which is what
    "was there an offer on X" and "when does the Onam offer start" need.
    """
    offers = [_decorate(o) for o in _DATA.get("offers", [])]

    if merchant:
        needle = str(merchant).strip().lower()
        offers = [
            o for o in offers
            if needle in o.get("merchant", "").lower()
            or needle in o.get("title", "").lower()
            or needle in o.get("id", "")
            or any(needle in b.get("merchant", "").lower() for b in o.get("bundle") or [])
        ]
    if category:
        wanted = str(category).strip().lower().replace(" ", "_")
        offers = [o for o in offers if o.get("category") == wanted]

    if not include_inactive:
        # "no period published" is kept: the bank lists these without dates,
        # and dropping them would answer "no offers on Cleartrip", which is
        # wrong in a way the user cannot detect.
        offers = [o for o in offers
                  if o.get("active") or o.get("status") == "no_period_published"]

    return {
        "available": True,
        "count": len(offers),
        "offers": offers,
        "today": _today().isoformat(),
        "note": (
            "Every discount above is the bank's published offer, not a "
            "calculation. Quote the dates with the discount: an offer whose "
            "status is expired or upcoming must not be described as available "
            "today. Merchant terms and conditions apply to all of them. "
            # A slab table is the one shape that got misread in testing: the
            # Rs 50,000-99,999 band (flat Rs 2,500) came back quoted as
            # Rs 5,000, which is the band above it. Where a slab or tier
            # carries `reads_as`, that sentence is already correct -- use it
            # rather than re-deriving the band from min_spend/max_spend.
            "Where an offer's slabs or tiers include a 'reads_as' sentence, "
            "quote that wording directly instead of working the band out from "
            "the numbers."
        ),
        **_provenance(),
    }


if __name__ == "__main__":
    print(json.dumps(get_card_offers(include_inactive=True), indent=2, ensure_ascii=False))

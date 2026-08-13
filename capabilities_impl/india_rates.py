"""India retail-banking reference rates, with provenance attached to every
number FinGuru is allowed to quote.

Why this is a curated file and not an API call: there is no free official
machine-readable feed for Indian deposit/lending rates. RBI publishes policy
decisions as press-release HTML and each bank publishes its own rate card as
a web page; scraping those means parsing markup that changes without notice,
on pages whose terms generally prohibit it. A hand-maintained file with an
explicit `as_of` per entry is the honest version of the same thing — it is
just as current as a scraper on the day you update it, and unlike a scraper
it can never silently report a misparsed number as fact.

Every getter returns `as_of`, `source_url`, `source_name` and a computed
`stale` flag alongside the value, and the skill instructions require FinGuru
to surface the date whenever it quotes one. `stale` goes True once the entry
is older than its own `max_age_days`, so a forgotten update degrades into a
visible caveat rather than a confident wrong answer.

Swapping this for a real feed later means re-registering the same capability
names in capabilities_impl/__init__.py against a new implementation — no
stage, agent, or skill file changes.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "india_reference_rates.json"
_RATES = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _today() -> date:
    return datetime.now().date()


def _provenance(entry: dict[str, Any]) -> dict[str, Any]:
    """The source/freshness envelope that rides along with every quoted
    number. `age_days` and `stale` are computed here rather than stored so
    they can't drift out of date the way a written-down flag would.

    Two dates come back and they are not interchangeable. `effective_from` is
    the bank's own w.e.f. date -- the one to tell the user, because it says
    when the rate actually changed. `as_of` is when we last confirmed it
    against the source page, and is only what staleness is measured from.
    Reporting `as_of` as if it were the rate's own date reads as "this rate
    is from June" when all that happened in June was someone re-checking it.
    """
    as_of_raw = entry.get("as_of")
    max_age = entry.get("max_age_days")
    age_days: int | None = None
    stale = False
    try:
        as_of = datetime.strptime(as_of_raw, "%Y-%m-%d").date()
        age_days = (_today() - as_of).days
        stale = max_age is not None and age_days > max_age
    except (TypeError, ValueError):
        # An unparseable as_of is itself a reason not to trust the entry.
        stale = True

    return {
        "effective_from": entry.get("effective_from"),
        "as_of": as_of_raw,
        "age_days": age_days,
        "stale": stale,
        "source_url": entry.get("source_url"),
        "source_name": entry.get("source_name"),
        "note": entry.get("note"),
    }


def get_policy_rate() -> dict:
    """RBI's policy repo rate — the anchor most retail rates move against."""
    entry = _RATES["policy"]["repo_rate_percent"]
    return {"repo_rate_percent": entry["value"], **_provenance(entry)}


def get_savings_rate() -> dict:
    """Representative savings-account rate. Deliberately labelled as one
    bank's rate, not "the" rate — savings rates vary widely by bank.
    """
    entry = _RATES["deposits"]["savings_account_percent"]
    return {"annual_rate_percent": entry["value"], **_provenance(entry)}


def get_fd_rate(tenure_months: int, senior_citizen: bool = False) -> dict:
    """The FD rate for a tenure, from the bracket table.

    Returns an explicit `available: False` for a tenure outside the published
    brackets rather than clamping to the nearest one — quoting the 10-year
    rate for a 30-year deposit would be a fabricated number wearing a real
    number's clothes.
    """
    try:
        tenure_months = int(tenure_months)
    except (TypeError, ValueError):
        return {"available": False, "reason": f"tenure_months must be a whole number of months, got {tenure_months!r}"}
    if tenure_months <= 0:
        return {"available": False, "reason": "tenure_months must be greater than zero"}

    entry = _RATES["deposits"]["fixed_deposit_general"]
    match = next(
        (b for b in entry["brackets"] if b["min_months"] <= tenure_months <= b["max_months"]),
        None,
    )
    if match is None:
        covered = f'{entry["brackets"][0]["min_months"]}-{entry["brackets"][-1]["max_months"]} months'
        return {
            "available": False,
            "reason": f"No published bracket covers {tenure_months} months (table covers {covered})",
            **_provenance(entry),
        }

    # The senior-citizen premium is not uniform: SBI pays +50 bps on most
    # tenures but +100 bps on 5 years and above (the WeCare band). A single
    # flat bonus quotes a 5-year senior deposit a full percentage point light,
    # so a bracket may override the table-wide default.
    bonus = 0.0
    if senior_citizen:
        bonus = match.get(
            "senior_citizen_bonus_percent",
            entry.get("senior_citizen_bonus_percent", 0.0),
        )
    return {
        "available": True,
        "tenure_months": tenure_months,
        "annual_rate_percent": round(match["rate_percent"] + bonus, 2),
        "base_rate_percent": match["rate_percent"],
        "senior_citizen_bonus_percent": bonus,
        "bracket_months": f'{match["min_months"]}-{match["max_months"]}',
        # Carried on every result, not just senior ones: the invented "super
        # senior" band showed up in an answer to someone who never used the
        # word, so the correction has to be present before the model reaches
        # for a category of its own.
        "senior_citizen_min_age_years": entry.get("senior_citizen_min_age_years"),
        "senior_citizen_note": entry.get("senior_citizen_note"),
        **_provenance(entry),
    }


def get_loan_rate(product: str) -> dict:
    """Indicative lending rate for a retail product — a floor, and a ceiling
    only where one is actually published.

    Never a single bare number, because the advertised floor is what a bank's
    best-profile borrower gets and presenting it as "your rate" is the single
    most misleading thing a finance bot can do.

    Most banks publish "X% p.a. onwards" and no upper bound. Where that is the
    case `to_percent` comes back None with `ceiling_published: False`, and the
    honest answer is that the bank doesn't publish a ceiling — not a plausible
    invented one. Where a ceiling exists, `ceiling_source` says whether it came
    from the bank's own card or from aggregator coverage, because those deserve
    different confidence.
    """
    entry = _RATES["lending_indicative"]
    products = entry["products"]
    key = str(product or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in products:
        return {
            "available": False,
            "reason": f"No indicative rate held for product '{product}'",
            "known_products": sorted(products),
        }
    band = products[key]
    ceiling = band.get("to_percent")
    return {
        "available": True,
        "product": key,
        "from_percent": band["from_percent"],
        "to_percent": ceiling,
        "ceiling_published": ceiling is not None,
        "ceiling_source": band.get("ceiling_source") if ceiling is not None else None,
        "is_indicative_range": True,
        **_provenance(entry),
    }


_SCHEME_GROUPS = ("small_savings", "jan_suraksha", "financial_inclusion")


def _scheme_names(key: str | None = None) -> dict:
    """Official names, so the model quotes a proper noun instead of recalling
    one. It invents otherwise -- PPF has come back glossed as two different
    non-existent Hindi names, and सुकन्या as सुकनी.

    Returned for every call, including the no-argument one, because the
    "which schemes suit me" answer is exactly where the invented names showed
    up: it names several schemes it did not individually look up.
    """
    names = _RATES["government_schemes"].get("names", {})
    if key is None:
        return {k: v for k, v in names.items() if not k.startswith("_")}
    # Rate fields are stored as "<scheme>_percent"; names are keyed by scheme.
    stem = key.removesuffix("_percent").removesuffix("_maturity_months")
    entry = names.get(stem)
    return {stem: entry} if entry else {}

# Schemes are known by their acronym far more often than by their full name --
# nobody says "Senior Citizens Savings Scheme" or "Kisan Vikas Patra" when
# "SCSS" and "KVP" will do, and the model reaches for whatever the user typed.
# Without these, get_scheme_details("scss") reports no such scheme while the
# rate sits right there under senior_citizens_savings_percent.
_SCHEME_ALIASES = {
    "scss": "senior_citizens_savings",
    "senior_citizen_savings": "senior_citizens_savings",
    "ssy": "sukanya_samriddhi",
    "sukanya": "sukanya_samriddhi",
    "kvp": "kisan_vikas_patra",
    "pomis": "post_office_monthly_income",
    "mis": "post_office_monthly_income",
    "public_provident_fund": "ppf",
    "jan_dhan": "pmjdy",
    "jandhan": "pmjdy",
    "mudra": "mudra_pmmy",
    "pmmy": "mudra_pmmy",
    "jeevan_jyoti": "pmjjby",
    "suraksha_bima": "pmsby",
    "atal_pension": "apy",
    "atal_pension_yojana": "apy",
}


def get_scheme_details(scheme: str | None = None) -> dict:
    """Government savings, insurance and credit schemes — PPF, Sukanya
    Samriddhi, SCSS, NSC, KVP, PMJJBY, PMSBY, APY, PMJDY and MUDRA.

    These matter most to exactly the person a general assistant serves worst:
    someone opening a first account, who needs to know that ₹20 a year buys
    ₹2,00,000 of accident cover. Passing no `scheme` returns everything, which
    is the right call for "what schemes am I eligible for" — the alternative
    is the model guessing scheme names one at a time.

    Small savings rates are revised quarterly, so `stale` here means something
    sharper than elsewhere in this file: past max_age_days the figure may have
    survived a rate review nobody checked.
    """
    entry = _RATES["government_schemes"]
    provenance = _provenance(entry)

    if scheme is None:
        return {
            "available": True,
            "schemes": {group: copy.deepcopy(entry[group]) for group in _SCHEME_GROUPS},
            "names": _scheme_names(),
            **provenance,
        }

    key = str(scheme).strip().lower().replace(" ", "_").replace("-", "_")
    key = _SCHEME_ALIASES.get(key, key)
    # Accept either a group name ("small_savings") or a single scheme
    # ("ppf", "pmjjby"), because the model reasonably reaches for both.
    if key in _SCHEME_GROUPS:
        return {"available": True, "scheme": key,
                "details": copy.deepcopy(entry[key]),
                "names": _scheme_names(), **provenance}

    for group in _SCHEME_GROUPS:
        block = entry[group]
        for name, value in block.items():
            if name == key or name == f"{key}_percent" or name.startswith(f"{key}_"):
                return {"available": True, "scheme": name, "group": group,
                        "details": copy.deepcopy(value),
                        "names": _scheme_names(name), **provenance}

    known = sorted(
        {g for g in _SCHEME_GROUPS}
        | {n for g in _SCHEME_GROUPS for n in entry[g] if n != "note"}
    )
    return {
        "available": False,
        "reason": f"No details held for scheme '{scheme}'",
        "known_schemes": known,
    }


def get_tax_saving_limits() -> dict:
    """Old-regime deduction ceilings. Regime-tagged because quoting an 80C
    limit to someone on the new regime is a wrong answer, not a caveat.
    """
    entry = _RATES["tax_saving_limits"]
    return {
        "regime": "old",
        "applies_to_new_regime": False,
        "limits": copy.deepcopy(entry["limits"]),
        **_provenance(entry),
    }

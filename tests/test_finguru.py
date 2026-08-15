"""FinGuru's capabilities and wiring.

The point of these tests is the grounding contract, not the prose: every
figure FinGuru is allowed to state must come from one of these functions, so
these are the things that have to be right. The LLM call itself is not
exercised here (see docs/testing.md — tests never need live Ollama); what's
verified is that the agent loads, that every capability it declares is
registered AND exposed as a tool schema, and that the math is correct.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import capabilities_impl  # noqa: F401 — registers the capabilities
from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.composition import load_agent
from agent_platform.runtime import chat
from agent_platform.stages.pipeline_stages import _TOOL_SCHEMAS
from capabilities_impl import customer_accounts, india_rates, money_math


# -- wiring ----

def test_agent_loads_with_its_skill():
    bundle = load_agent("finguru")
    assert bundle.definition.agent_id == "finguru"
    assert "finguru" in bundle.skills
    assert bundle.skills["finguru"].instructions_text.strip()


def test_every_declared_capability_is_registered_and_callable_as_a_tool():
    """A capability missing from _TOOL_SCHEMAS is silently dropped by
    reason_llm_with_tools — the model simply never sees it, and FinGuru
    quietly loses the ability to ground that kind of answer. That failure is
    invisible at runtime, so it gets caught here.
    """
    bundle = load_agent("finguru")
    for capability in bundle.definition.capabilities:
        assert DEFAULT_REGISTRY.has(capability.name), f"{capability.name} not registered"
        assert capability.name in _TOOL_SCHEMAS, f"{capability.name} has no tool schema"


def test_tool_schemas_name_themselves_consistently():
    for name, schema in _TOOL_SCHEMAS.items():
        assert schema["function"]["name"] == name


def test_output_contract_requires_grounding_fields():
    skill = load_agent("finguru").skills["finguru"]
    assert set(skill.output_contract["required"]) == {
        "language", "content_type", "content", "confidence", "follow_ups",
    }


def test_follow_ups_are_a_schema_field_not_a_marker_in_the_prose():
    """The chat UI used to get suggested questions by appending "write
    ###FOLLOWUPS### then three questions" to the message and parsing them back
    out of the reply. Measured, the model obeyed that sometimes: present for
    one English question, absent for another and for Hindi, within the same
    minute -- which read as "follow-ups are broken in English".

    As a required schema field it is what constrains the model's JSON, so it
    is always there. It also keeps the suggestions OUT of `content`, which
    matters for voice: a marker block inside the reply is a list of questions
    waiting to be read aloud.
    """
    contract = load_agent("finguru").skills["finguru"].output_contract
    field = contract["properties"]["follow_ups"]

    assert "follow_ups" in contract["required"]
    assert field["type"] == "array"
    assert field["items"]["type"] == "string"
    assert field["maxItems"] == 3
    # The failure mode that survived two prompt revisions: the model writing
    # its own questions to the user ("Would you like me to...") instead of the
    # user's next question to it. The instructions carry a table of both
    # forms; the contract has to at least say whose voice it is.
    assert "THE USER" in field["description"]


# -- vernacular ----
#
# FinGuru answers in the user's own language (Hindi, Tamil, Telugu, Bengali,
# Marathi, Kannada, romanized Hinglish...). The language behaviour itself is
# the model's and can't be asserted without a live LLM, but the contract that
# *drives* it is structural, and silently losing it would degrade every
# non-English answer without failing anything.

def test_language_is_declared_before_the_content_it_governs():
    """Ollama generates structured output in the schema's property order, so
    `language` sitting first is load-bearing, not cosmetic: the model commits
    to a language and script before it writes a word of `content`, instead of
    picking one retroactively. Reordering this weakens language adherence
    without breaking anything visibly, hence the test.
    """
    skill = load_agent("finguru").skills["finguru"]
    properties = list(skill.output_contract["properties"])
    assert properties[0] == "language"
    assert properties.index("language") < properties.index("content")


def test_agent_output_schema_matches_the_skill_contract():
    """agent.yaml's output_schema is what the UI renders; the skill's
    output_contract is what actually validates the model. They drift silently.
    """
    bundle = load_agent("finguru")
    declared = set(bundle.definition.output_schema.get("required", []))
    contract = set(bundle.skills["finguru"].output_contract["required"])
    assert declared <= contract, f"agent.yaml requires fields the contract doesn't: {declared - contract}"
    assert "language" in declared


def test_instructions_keep_tool_grounding_language_independent():
    """The failure mode this guards is specific: the model is likelier to
    answer a Hindi or Tamil question from memory than an English one, because
    tool-calling degrades faster than fluency does. The instructions have to
    say so explicitly, and in English -- the tool names and arguments are
    always English regardless of the reply language.
    """
    text = load_agent("finguru").skills["finguru"].instructions_text
    assert "Tool calls do not change with language" in text
    assert "Western digits" in text


def test_western_digits_rule_covers_tenures_and_not_only_money():
    """Observed on qwen3.6:35b: money was formatted perfectly (6.25%,
    ₹1,00,000) while the tenure right next to it came out as "১ বছরের", and
    the date as "১৫ ডিসেম্বর ২০২৫". Money survives because it is copied from a
    tool result already in Western digits; the figures the model composes
    itself are the ones that revert. The generic "use Western digits" line was
    present throughout and was not enough on its own, so the rule enumerates
    the categories that actually slip. Losing that enumeration reopens it.
    """
    text = load_agent("finguru").skills["finguru"].instructions_text
    assert "tenures, month counts, years and dates" in text


def test_maturity_values_are_required_to_come_from_the_tool():
    """The grounding rule that actually got broken. Asked a short FD question
    in Bengali, the model skipped money.fd_maturity and did principal × rate
    in its head -- ₹1,06,250 instead of ₹1,06,398.02, because Indian FDs
    compound quarterly. It answered the same question correctly whenever it
    called the tool. The instruction now names that exact shortcut, since the
    general "don't do mental math" line had been there all along and lost.
    """
    text = load_agent("finguru").skills["finguru"].instructions_text
    assert "money.fd_maturity" in text
    assert "compounds quarterly" in text or "compound" in text.lower()


def test_instructions_do_not_carry_superseded_rates_as_examples():
    """The instructions used 6.6% and ₹2,13,530.31 as worked examples. Those
    came from a rate card that was wrong by 35 bps, and a model reading them
    repeatedly has a plausible wrong number sitting in its context next to a
    rule telling it to trust tools. Examples have to track the fixture.
    """
    text = load_agent("finguru").skills["finguru"].instructions_text
    for superseded in ("6.6%", "2,13,530"):
        assert superseded not in text, f"{superseded} is a superseded rate figure"


# -- India reference rates: provenance is part of the return value ----

@pytest.mark.parametrize("getter", [
    india_rates.get_policy_rate,
    india_rates.get_savings_rate,
    india_rates.get_tax_saving_limits,
])
def test_rate_lookups_carry_provenance(getter):
    result = getter()
    assert result["as_of"], "every quoted figure must carry its as-of date"
    assert result["source_url"], "every quoted figure must carry its source"
    assert isinstance(result["stale"], bool)


@pytest.mark.parametrize("getter", [
    india_rates.get_policy_rate,
    india_rates.get_savings_rate,
    india_rates.get_tax_saving_limits,
])
def test_rate_lookups_distinguish_the_banks_date_from_our_check_date(getter):
    """These are different facts and the agent quotes the first one. Collapsing
    them told users a rate had "just" changed when all that happened was
    someone re-opening the source page — observed live against a rate that had
    been unchanged for eight months.
    """
    result = getter()
    assert result["effective_from"], "the bank's own w.e.f. date must survive to the answer"
    assert result["effective_from"] <= result["as_of"], "a rate cannot take effect after we checked it"


def test_fd_rate_picks_the_right_bracket():
    assert india_rates.get_fd_rate(12)["bracket_months"] == "12-23"
    assert india_rates.get_fd_rate(23)["bracket_months"] == "12-23"
    assert india_rates.get_fd_rate(24)["bracket_months"] == "24-35"


def test_fd_rate_adds_senior_bonus_only_when_asked():
    base = india_rates.get_fd_rate(12)
    senior = india_rates.get_fd_rate(12, senior_citizen=True)
    assert base["senior_citizen_bonus_percent"] == 0.0
    assert senior["annual_rate_percent"] == pytest.approx(base["annual_rate_percent"] + 0.5)


def test_fd_rate_refuses_uncovered_tenure_instead_of_clamping():
    """Clamping a 30-year request to the 10-year bracket would hand back a
    fabricated number that looks exactly like a real one.
    """
    result = india_rates.get_fd_rate(360)
    assert result["available"] is False
    assert "360" in result["reason"]


@pytest.mark.parametrize("bad", [0, -5, "soon", None])
def test_fd_rate_rejects_nonsense_tenures(bad):
    assert india_rates.get_fd_rate(bad)["available"] is False


def test_fd_rate_uses_the_bracket_specific_senior_bonus_where_one_exists():
    """SBI pays +50 bps to seniors on most tenures but +100 bps from 5 years
    (the WeCare band). A flat table-wide bonus quotes a 5-year senior deposit
    a full percentage point light.
    """
    base = india_rates.get_fd_rate(60)
    senior = india_rates.get_fd_rate(60, senior_citizen=True)
    assert senior["annual_rate_percent"] == pytest.approx(base["annual_rate_percent"] + 1.0)


def test_loan_rate_is_never_a_single_bare_rate():
    result = india_rates.get_loan_rate("personal_loan")
    assert result["is_indicative_range"] is True
    assert result["from_percent"] < result["to_percent"]


def test_loan_rate_reports_an_unpublished_ceiling_instead_of_inventing_one():
    """SBI publishes most lending rates as "X% onwards" with no upper bound.
    Filling that gap with a plausible number is exactly the failure this whole
    fixture exists to prevent, so the absence has to be representable.
    """
    result = india_rates.get_loan_rate("car_loan")
    assert result["available"] is True
    assert result["to_percent"] is None
    assert result["ceiling_published"] is False


def test_a_published_ceiling_says_where_it_came_from():
    """A ceiling sourced from a rate aggregator must not be attributed to the
    bank's own rate card — the user checking it against sbi.bank.in won't
    find it there.
    """
    result = india_rates.get_loan_rate("home_loan")
    assert result["ceiling_published"] is True
    assert result["ceiling_source"] == "aggregator"


def test_loan_rate_lists_known_products_when_asked_for_an_unknown_one():
    result = india_rates.get_loan_rate("yacht_loan")
    assert result["available"] is False
    assert "home_loan" in result["known_products"]


# -- government schemes ----

def test_scheme_tool_is_registered_and_schemad():
    assert DEFAULT_REGISTRY.has("india.get_scheme_details")
    assert "india.get_scheme_details" in _TOOL_SCHEMAS
    declared = {c.name for c in load_agent("finguru").definition.capabilities}
    assert "india.get_scheme_details" in declared


def test_asking_for_nothing_returns_every_scheme():
    """"What schemes can I get?" is the actual question people ask, and the
    alternative to answering it in one call is the model guessing scheme names
    one at a time until something sticks.
    """
    result = india_rates.get_scheme_details()
    assert result["available"] is True
    assert set(result["schemes"]) == {"small_savings", "jan_suraksha", "financial_inclusion"}


@pytest.mark.parametrize("query,expected_in", [
    ("ppf", 7.1),
    ("sukanya_samriddhi", 8.2),
    ("scss", None),                 # resolves via the _percent suffix
    ("small_savings", None),        # a whole group
])
def test_a_scheme_resolves_by_short_name_or_by_group(query, expected_in):
    result = india_rates.get_scheme_details(query)
    assert result["available"] is True, f"{query} did not resolve"
    if expected_in is not None:
        assert result["details"] == expected_in


def test_unknown_scheme_lists_the_real_ones_instead_of_guessing():
    result = india_rates.get_scheme_details("mango_yojana")
    assert result["available"] is False
    assert "ppf" in " ".join(result["known_schemes"]).lower()


def test_scheme_figures_carry_the_quarterly_review_provenance():
    """Small savings rates are revised EVERY QUARTER by the Department of
    Economic Affairs. That is far faster than anything else in this fixture,
    so the entry needs a max_age_days short enough to flag itself within one
    review cycle — a stale PPF rate is a wrong number, not a dated one.
    """
    result = india_rates.get_scheme_details("ppf")
    assert result["effective_from"] and result["as_of"]
    entry = india_rates._RATES["government_schemes"]
    assert entry["max_age_days"] <= 100, "must flag itself within one quarterly review"


def test_pmsby_states_it_covers_accidents_only():
    """The single most damaging confusion available here: PMSBY is ₹20/year
    and PMJJBY is ₹436/year, and someone told the cheap one covers death by
    any cause will believe their family is insured when it is not. The
    distinction has to be carried in the data, not left to the model.
    """
    pmsby = india_rates.get_scheme_details("pmsby")["details"]
    pmjjby = india_rates.get_scheme_details("pmjjby")["details"]
    assert "accident" in pmsby["cover"].lower()
    assert "not" in pmsby["cover"].lower() and "illness" in pmsby["cover"].lower()
    assert "any cause" in pmjjby["cover"].lower()
    assert pmsby["annual_premium_rupees"] < pmjjby["annual_premium_rupees"]


def test_apy_refuses_to_carry_a_contribution_figure():
    """APY contributions depend on entry age and chosen pension — a single
    number would be wrong for almost everyone. The fixture holds the pension
    options and an explicit instruction not to quote a contribution.
    """
    apy = india_rates.get_scheme_details("apy")["details"]
    assert "monthly_pension_options_rupees" in apy
    assert not any("contribution" in k for k in apy if k != "note")
    assert "age-dependent" in apy["note"] or "age" in apy["note"]


def test_tax_limits_are_tagged_to_the_old_regime():
    result = india_rates.get_tax_saving_limits()
    assert result["regime"] == "old"
    assert result["applies_to_new_regime"] is False
    assert result["limits"]["section_80c_rupees"] == 150000


def test_tax_limits_are_copied_not_shared():
    """A caller mutating what it got back must not corrupt the module-level
    fixture for every later lookup.
    """
    first = india_rates.get_tax_saving_limits()
    first["limits"]["section_80c_rupees"] = 1
    assert india_rates.get_tax_saving_limits()["limits"]["section_80c_rupees"] == 150000


def test_stale_entries_are_flagged(monkeypatch):
    import datetime as real_datetime

    monkeypatch.setattr(india_rates, "_today", lambda: real_datetime.date(2099, 1, 1))
    assert india_rates.get_policy_rate()["stale"] is True


# -- money math: the numbers themselves ----

def test_fd_maturity_matches_the_quarterly_compounding_formula():
    result = money_math.fd_maturity(100000, 6.6, 12)
    expected = 100000 * (1 + 6.6 / 400) ** 4
    assert result["maturity_value"] == pytest.approx(expected, abs=0.01)
    assert result["interest_earned"] == pytest.approx(expected - 100000, abs=0.01)


def test_sip_projection_is_an_annuity_due():
    result = money_math.sip_projection(10000, 12, 10)
    i, n = 0.01, 120
    expected = 10000 * (((1 + i) ** n - 1) / i) * (1 + i)
    assert result["projected_value"] == pytest.approx(expected, abs=0.01)
    assert result["total_invested"] == pytest.approx(1200000)
    assert result["is_projection_not_guarantee"] is True


def test_sip_required_for_goal_inverts_sip_projection():
    """The two must agree, or a user asking the same question two ways gets
    two different answers.
    """
    required = money_math.sip_required_for_goal(10_000_000, 12, 15)["required_monthly_investment"]
    back = money_math.sip_projection(required, 12, 15)["projected_value"]
    assert back == pytest.approx(10_000_000, rel=1e-6)


def test_debt_payoff_time_matches_the_amortisation_formula():
    result = money_math.debt_payoff_time(240000, 42, 12000)
    r = 0.42 / 12
    expected = -math.log(1 - (r * 240000) / 12000) / math.log(1 + r)
    assert result["months_to_clear"] == math.ceil(expected)
    assert result["total_interest"] > 0


def test_debt_payoff_reports_when_a_payment_never_clears_the_debt():
    """The genuinely useful answer for someone paying under the interest —
    a months figure here would be nonsense (or a math domain error).
    """
    result = money_math.debt_payoff_time(240000, 42, 5000)
    assert result["ok"] is False
    assert result["minimum_payment_to_make_progress"] == pytest.approx(8400, abs=1)


def test_prepayment_savings_is_the_difference_between_two_payoff_schedules():
    """Ties the new calculator to the already-verified amortisation formula
    rather than reimplementing it — if debt_payoff_time is right, this is."""
    args = dict(balance=320000, annual_rate_percent=14, monthly_payment=10000)
    result = money_math.prepayment_savings(**args, prepay_amount=40000)
    before = money_math.debt_payoff_time(**args)
    after = money_math.debt_payoff_time(280000, 14, 10000)

    assert result["interest_saved_gross"] == pytest.approx(
        before["total_interest"] - after["total_interest"], abs=0.01)
    assert result["months_saved"] == before["months_to_clear"] - after["months_to_clear"]
    assert result["interest_saved_gross"] > 0


def test_prepayment_charge_is_netted_off_with_gst_on_the_fee():
    """The obvious mental arithmetic — "3% of 50,000 is 1,500" — is wrong,
    because the prepayment charge is a fee and attracts 18% GST. Quoting the
    gross saving, or the charge without GST, overstates what the customer
    keeps by exactly the tax. This is why the charge is computed here rather
    than left to the model.
    """
    result = money_math.prepayment_savings(318500, 14.25, 10480, 50000,
                                            prepayment_charge_percent=3.0)
    assert result["prepayment_charge"] == pytest.approx(1500.0)
    assert result["gst_on_charge"] == pytest.approx(270.0)
    assert result["total_charge_payable"] == pytest.approx(1770.0)
    assert result["net_saving"] == pytest.approx(result["interest_saved_gross"] - 1770.0, abs=0.01)
    assert result["worth_it"] is True


def test_a_zero_charge_loan_keeps_the_whole_saving():
    """RBI bars foreclosure charges on floating-rate home loans to
    individuals, so 0% is a real value and must not be treated as missing."""
    result = money_math.prepayment_savings(2840000, 8.5, 26350, 100000,
                                            prepayment_charge_percent=0.0)
    assert result["net_saving"] == result["interest_saved_gross"]
    assert result["total_charge_payable"] == 0


def test_prepaying_the_whole_balance_reports_closure_not_a_negative_schedule():
    """Prepaying >= the balance has no remaining schedule to compare against;
    the honest answer is "this closes the loan, ask for the foreclosure
    figure", not a computed saving on a debt that no longer exists.
    """
    result = money_math.prepayment_savings(100000, 14, 10000, 150000)
    assert result["ok"] is True
    assert result["clears_the_loan"] is True


def test_prepayment_savings_inherits_the_never_pays_off_refusal():
    """An EMI below the monthly interest has no payoff date, so there is
    nothing to save months against — it must surface that rather than
    returning a number."""
    result = money_math.prepayment_savings(320000, 14, 1000, 40000)
    assert result["ok"] is False
    assert "reason" in result


def test_budget_split_sums_back_to_income():
    result = money_math.budget_split(85000)
    assert sum(result["split"].values()) == pytest.approx(85000, abs=0.01)
    assert result["split"]["savings"] == pytest.approx(17000)


def test_budget_split_rejects_percentages_that_do_not_total_100():
    result = money_math.budget_split(85000, needs_percent=60, wants_percent=30, savings_percent=20)
    assert result["ok"] is False
    assert "110" in result["reason"]


def test_emergency_fund_target():
    result = money_math.emergency_fund_target(40000, 6)
    assert result["target_amount"] == pytest.approx(240000)


@pytest.mark.parametrize("fn,args", [
    (money_math.fd_maturity, (-100, 6.6, 12)),
    (money_math.fd_maturity, (100000, 6.6, 0)),
    (money_math.sip_projection, (5000, 12, "ten")),
    (money_math.debt_payoff_time, (0, 42, 12000)),
    (money_math.emergency_fund_target, (None, 6)),
])
def test_bad_inputs_return_a_reason_rather_than_raising(fn, args):
    """A malformed tool call from the model must come back as something it
    can explain, not an exception that kills the pipeline run.
    """
    result = fn(*args)
    assert result["ok"] is False
    assert result["reason"]


# -- account lookup, and why FinGuru no longer has it ----
#
# The capability still exists and still works; FinGuru just doesn't hold it
# any more. With no real per-customer data behind it, the only thing it could
# resolve was a demo fixture, and the model reached for that fixture ahead of
# what the user had actually typed.

def test_finguru_cannot_look_up_anyones_accounts():
    """The regression this guards is subtle and was found in a model
    benchmark, not in a test: asked about a ₹3,00,000 loan at ₹10,000 a
    month, FinGuru called accounts.get_profile and answered about the
    fixture's ₹3,18,500 at ₹10,480 instead. Every figure in that reply was
    correctly derived and every one was about someone else's loan — there is
    nothing in the output a reader could use to notice. Re-adding either tool
    to the agent brings that back.
    """
    declared = {c.name for c in load_agent("finguru").definition.capabilities}
    assert not declared & {"accounts.get_profile", "accounts.get_borrowings"}


def test_the_chat_layer_hands_no_customer_identity_to_the_pipeline():
    """The other half of the same fix: chat.py used to inject a default
    customer_id so the account tools had someone to resolve. A default
    identity is the bug — production would supply a real authenticated one
    or none at all.
    """
    source = Path(chat.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_DEMO_CUSTOMER" not in source


def test_account_tools_are_registered_and_schemad():
    """Still true — the implementation is intact for any agent that has a
    genuine authenticated identity to pass it. Only FinGuru's use is gone.
    """
    for name in ("accounts.get_profile", "accounts.get_borrowings"):
        assert DEFAULT_REGISTRY.has(name), f"{name} not registered"
        assert name in _TOOL_SCHEMAS, f"{name} has no tool schema"


def test_profile_carries_the_terms_a_real_answer_turns_on():
    """Balance alone isn't enough: "should I prepay" needs the rate, the EMI
    and the prepayment charge, and those are exactly what a generic
    assistant has to ask three questions to get."""
    profile = customer_accounts.get_profile()
    assert profile["available"] is True
    assert profile["loans"]
    for loan in profile["loans"]:
        for field in ("outstanding_principal", "annual_rate_percent", "emi",
                      "remaining_tenure_months", "prepayment_charge_percent"):
            assert field in loan, f"{loan['type']} is missing {field}"


def test_borrowings_are_ordered_most_expensive_first():
    """The ordering is the answer to "which should I clear first" — if it
    regresses, the agent's priority advice silently inverts."""
    rates = [b["annual_rate_percent"] for b in customer_accounts.get_borrowings()["borrowings"]]
    assert rates == sorted(rates, reverse=True)


def test_no_account_or_card_numbers_are_ever_returned():
    """Identifiers reach a model's context, then its logs, then its reply.
    The agent needs balances and terms to reason and never needs a number
    that could be used against the account, so none are returned at all.
    """
    blob = json.dumps([customer_accounts.get_profile(), customer_accounts.get_borrowings()])
    for banned in ("pan", "aadhaar", "card_number", "account_number", "ifsc", "cvv"):
        assert banned not in blob.lower(), f"{banned} leaked into a capability return"


def test_unknown_customer_is_refused_rather_than_defaulted():
    """Falling back to the demo customer on an unrecognised id would show
    one person another person's accounts — the worst possible failure here.
    """
    result = customer_accounts.get_profile("NOT_A_CUSTOMER")
    assert result["available"] is False
    assert "reason" in result


def test_every_scheme_carries_an_official_name_in_both_languages():
    """Scheme names are proper nouns, and left to recall the model invents
    them: PPF has come back glossed as two different non-existent Hindi
    names. The tool has to supply the string so the answer can quote it.
    """
    names = india_rates.get_scheme_details()["names"]
    for key, entry in names.items():
        assert entry.get("en"), f"{key} has no English name"
        assert entry.get("hi"), f"{key} has no Hindi name"


def test_a_single_scheme_lookup_carries_its_own_name():
    """The no-argument call is not the only path that needs names — asking
    for one scheme is the common case and must not come back nameless."""
    for query, expected_key in (("ppf", "ppf"), ("scss", "senior_citizens_savings"),
                                ("sukanya", "sukanya_samriddhi"), ("pmjjby", "pmjjby")):
        result = india_rates.get_scheme_details(query)
        assert result["available"] is True
        assert expected_key in result["names"], f"{query} returned no name"


def test_hindi_scheme_names_are_not_invented_expansions():
    """The observed failures, pinned. Acronym-only schemes stay acronyms in
    Hindi because that is what people say out loud; the fabricated glosses
    and the misspelling must never come back.
    """
    names = india_rates.get_scheme_details()["names"]
    assert names["ppf"]["hi"] == "PPF"
    assert names["nsc"]["hi"] == "NSC"
    assert names["sukanya_samriddhi"]["hi"] == "सुकन्या समृद्धि योजना"
    blob = json.dumps(names, ensure_ascii=False)
    for invented in ("सामान्य जनता विकास खाता", "प्रगतिशील", "सुकनी"):
        assert invented not in blob, f"invented name {invented!r} present in the fixture"


def test_fd_rate_states_the_senior_age_and_denies_a_super_senior_band():
    """A Hindi answer put a 62-year-old in a 'super senior' deposit category
    that SBI does not have, and credited the We-care bonus to it. The
    threshold and the correction ride on every FD result so the model reads
    them before inventing a tier.
    """
    for senior in (False, True):
        result = india_rates.get_fd_rate(60, senior_citizen=senior)
        assert result["senior_citizen_min_age_years"] == 60
        assert "super senior" in result["senior_citizen_note"].lower()


def test_instructions_pin_the_colloquial_hindi_register():
    """Textbook Hindi reads like a circular to the people this is for.

    The rows asserted here are the counter-intuitive half, measured over
    1,518 scraped passages: products keep their English names, but the
    *concepts* stay Hindi. An earlier hand-written version of this table had
    सेविंग and इन्वेस्ट as the colloquial forms, which the corpus contradicts
    -- real speakers write बचत and निवेश. Reaching for the English word to
    sound casual overshoots, and that is the mistake worth pinning.
    """
    text = (Path("skills_library/finguru/instructions.md")).read_text(encoding="utf-8")
    for english_product in ("लोन", "टैक्स", "बैलेंस"):
        assert english_product in text, f"{english_product} missing from the register table"
    for hindi_concept in ("ब्याज", "निवेश", "बचत"):
        assert hindi_concept in text, f"{hindi_concept} missing from the register table"
    assert "इंटरेस्ट" in text, "the overshoot case (इंटरेस्ट) should be named as wrong"
    assert "names" in text and "get_scheme_details" in text

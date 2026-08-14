"""Registers the mock capability implementations against the shared
ToolRegistry. This is the only file that changes when a mock is swapped for
a real MCP-backed tool: replace the `fn` passed to `register`, keep the
name.
"""
from agent_platform.capabilities import DEFAULT_REGISTRY

from . import (
    card_offers,
    credit_bureau,
    customer_accounts,
    doc_search,
    financial_tools,
    fx_rates,
    india_rates,
    kyc,
    lead_data,
    lead_search,
    money_math,
    reporting,
)

DEFAULT_REGISTRY.register(
    "lead_data.get_lead", lead_data.get_lead,
    "Look up an SME lead's profile and financials by lead_id",
)
DEFAULT_REGISTRY.register(
    "credit_bureau.get_report", credit_bureau.get_bureau_report,
    "Pull a credit bureau report by lead_id",
)
DEFAULT_REGISTRY.register(
    "kyc.get_status", kyc.get_kyc_status,
    "Look up KYC/KYB verification and sanctions-screening status by lead_id",
)
DEFAULT_REGISTRY.register(
    "lead_discovery.search_leads", lead_search.search_leads,
    "Search the SME lead pool by industry, location, business need and minimum turnover",
)
DEFAULT_REGISTRY.register(
    "finance.get_fd_rate", financial_tools.get_fd_rate,
    "Look up the current fixed deposit interest rate for a given tenure in months",
)
DEFAULT_REGISTRY.register(
    "finance.get_savings_rate", financial_tools.get_savings_rate,
    "Look up the current savings account interest rate",
)
DEFAULT_REGISTRY.register(
    "finance.calculate_emi", financial_tools.calculate_emi,
    "Calculate the monthly EMI, total payment, and total interest for a loan",
)
DEFAULT_REGISTRY.register(
    "reports.lead_pipeline_digest", reporting.lead_pipeline_digest,
    "Get real, computed SME lead-pipeline statistics for a location and/or industry",
)

# -- FinGuru: India retail banking reference data ----
# Curated, provenance-carrying rates. Every return value includes as_of,
# source_url and a computed `stale` flag — see capabilities_impl/india_rates.py
# for why these aren't live API calls. Swapping in a real feed later means
# re-pointing these same names at a new implementation.
DEFAULT_REGISTRY.register(
    "india.get_policy_rate", india_rates.get_policy_rate,
    "Look up the RBI policy repo rate, with the date it was last updated",
)
DEFAULT_REGISTRY.register(
    "india.get_savings_rate", india_rates.get_savings_rate,
    "Look up the representative savings-account interest rate, with its as-of date",
)
DEFAULT_REGISTRY.register(
    "india.get_fd_rate", india_rates.get_fd_rate,
    "Look up the fixed-deposit rate for a tenure in months, optionally with the senior-citizen bonus",
)
DEFAULT_REGISTRY.register(
    "india.get_loan_rate", india_rates.get_loan_rate,
    "Look up the indicative interest-rate range for a retail loan product",
)
DEFAULT_REGISTRY.register(
    "india.get_tax_saving_limits", india_rates.get_tax_saving_limits,
    "Look up old-regime tax deduction ceilings (80C, 80D, NPS, home-loan interest)",
)
DEFAULT_REGISTRY.register(
    "india.get_scheme_details", india_rates.get_scheme_details,
    "Look up government scheme terms — PPF, Sukanya Samriddhi, SCSS, NSC, KVP, "
    "PMJJBY, PMSBY, APY, PMJDY, MUDRA",
)
DEFAULT_REGISTRY.register(
    "india.get_card_offers", card_offers.get_card_offers,
    "Look up current SBI debit card merchant offers and discounts — Amazon, "
    "BigBasket, Flipkart, Cleartrip, Apollo 24|7, Reliance Digital — with each "
    "offer's validity dates checked against today",
)

# -- FinGuru: live market data ----
DEFAULT_REGISTRY.register(
    "fx.get_rate", fx_rates.get_fx_rate,
    "Get the live ECB reference exchange rate for a currency pair",
)

# -- FinGuru: deterministic personal-finance math ----
DEFAULT_REGISTRY.register(
    "money.fd_maturity", money_math.fd_maturity,
    "Compute a fixed deposit's maturity value and interest earned (quarterly compounding)",
)
DEFAULT_REGISTRY.register(
    "money.sip_projection", money_math.sip_projection,
    "Project the future value of a monthly SIP at an assumed annual return",
)
DEFAULT_REGISTRY.register(
    "money.sip_required_for_goal", money_math.sip_required_for_goal,
    "Compute the monthly SIP needed to reach a target amount in a given number of years",
)
DEFAULT_REGISTRY.register(
    "money.debt_payoff_time", money_math.debt_payoff_time,
    "Compute how long a debt takes to clear at a fixed monthly payment, and total interest paid",
)
DEFAULT_REGISTRY.register(
    "money.prepayment_savings", money_math.prepayment_savings,
    "Compute months and interest saved by a one-off lump-sum prepayment against a loan",
)
DEFAULT_REGISTRY.register(
    "money.budget_split", money_math.budget_split,
    "Split monthly take-home pay across needs/wants/savings",
)
DEFAULT_REGISTRY.register(
    "money.emergency_fund_target", money_math.emergency_fund_target,
    "Compute an emergency-fund target from monthly essential expenses",
)

# -- FinGuru: the signed-in customer's own position ----
DEFAULT_REGISTRY.register(
    "accounts.get_profile", customer_accounts.get_profile,
    "Read the signed-in customer's actual deposits, loans and cards with their real terms",
)
DEFAULT_REGISTRY.register(
    "accounts.get_borrowings", customer_accounts.get_borrowings,
    "Read the signed-in customer's debts, ordered most expensive first",
)

# -- FinGuru: published RBI guidance (rules, not numbers) ----
DEFAULT_REGISTRY.register(
    "docs.search", doc_search.search,
    "Search published RBI guidance for the rule that answers a question, with its source",
)
DEFAULT_REGISTRY.register(
    "docs.list_sources", doc_search.list_sources,
    "List which documents the guidance corpus covers, and how current it is",
)

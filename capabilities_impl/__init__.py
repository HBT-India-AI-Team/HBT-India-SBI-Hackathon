"""Registers the mock capability implementations against the shared
ToolRegistry. This is the only file that changes when a mock is swapped for
a real MCP-backed tool: replace the `fn` passed to `register`, keep the
name.
"""
from agent_platform.capabilities import DEFAULT_REGISTRY

from . import credit_bureau, kyc, lead_data, lead_search

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

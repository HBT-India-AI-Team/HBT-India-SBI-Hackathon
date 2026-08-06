"""One live-Ollama smoke test — no fake adapter fixture in this file, so
it makes real calls. Skipped by default (real network dependency, ~30-90s,
occasionally hits transient 503s on the shared demo tunnel — see
docs/running.md). To run it before a demo, comment out the `@pytest.mark.
skip` line below and run:

    python -m pytest tests/test_live_workflow_smoke.py -v
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.workflows import run_workflow


@pytest.mark.skip(reason="requires a live, reachable Ollama server — run manually before a demo")
def test_live_workflow_smoke():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "manufacturing", "location": "Chennai", "business_need": "working_capital",
    })
    assert result["status"] == "COMPLETED"
    assert result["discovery"]["selected_lead_id"] == "SME-1001"

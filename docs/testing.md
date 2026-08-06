# Testing

```powershell
python -m pytest tests/ -v
```

55 tests (+1 intentionally skipped live-only test), ~1.5 seconds, zero network calls by default. That's deliberate: every test that needs an LLM response monkeypatches the adapter instead of hitting Ollama, so the suite is fast and deterministic in CI or offline.

## Files

- **`tests/test_rules_engine.py`** — the deterministic engine in isolation: dotted-field lookup, gate severity ordering (`NOT_QUALIFIED` beats `NEEDS_HUMAN_REVIEW` when both fail), weighted-average scoring, missing-field handling, composite weighting, product ranking by specificity.
- **`tests/test_output_validator.py`** — the LLM-output safety net: valid output passes through unchanged, forbidden fields (`decision`, `outcome`, ...) get stripped without failing the run, missing required fields or out-of-range confidence force a retry, individually ungrounded citations get dropped (not fatal), *all* citations ungrounded forces a retry, the deterministic fallback shape.
- **`tests/test_pipeline_end_to_end.py`** — full `invoke_agent()` runs against the Lead Qualification Agent with a fake LLM adapter. Covers all five outcome paths (qualified / conditional / HITL via bureau floor / rejected via sanctions / rejected via multiple gates), an unknown lead producing a captured error rather than a crash, an LLM outage correctly degrading to the deterministic fallback *and* escalating to HITL via low confidence, and confirms every run gets a persisted record with the right fields.
- **`tests/test_reusability.py`** — the actual proof that the runtime is reusable, not just described that way: `echo_probe` (a second, unrelated agent — different skill, 2-stage pipeline, no LLM, no capabilities) is discoverable by the same loader, runs through the same executor, and its own input-validation errors come from its own schema, not lead_qualification's.
- **`tests/test_lead_discovery.py`** — search filtering (industry/location/business_need/active), ranking correctness and ordering, `limit` capping, graceful "no candidates" handling, persistence.
- **`tests/test_proposal.py`** — product ranking among only the eligible products passed in, `fit_score` derivation, `NO_PRODUCT_MATCH` when nothing's eligible, `required_documents` being deterministic (never LLM-sourced), and that Proposal genuinely cannot produce a qualification-shaped decision.
- **`tests/test_commercial_leadgen_workflow.py`** — the full branching matrix with a fake LLM: strong lead proceeds through to a generated proposal, weak lead is rejected and proposal is skipped, borderline lead needs human review and proposal is skipped, no matching candidates fails the workflow cleanly, and a workflow run's persisted record carries every child agent's `run_id` (each independently addressable at `/runs/{run_id}`).
- **`tests/test_agent_router.py`** — the LLM-based routing workflow (`docs/agent-router.md`): registration, the catalog correctly excludes non-`routable` agents, a confident pick invokes the chosen agent end-to-end, low confidence and an LLM outage both resolve to `NEEDS_CLARIFICATION` without invoking anything, a pick outside the known catalog is rejected defensively even though the schema already constrains it, a confidently-routed agent whose own run errors is surfaced as `AGENT_FAILED` rather than swallowed, the run is persisted like any other, and an explicit `agent_id` override skips the LLM call entirely (and is validated the same way an LLM pick would be).
- **`tests/test_live_workflow_smoke.py`** — the one live-Ollama test, skipped by default (real network dependency, ~30-90s). Comment out its `@pytest.mark.skip` to run it manually before a demo.
- **`tests/fakes.py`** — `FakeAdapter`/`FailingAdapter`, shared by every test above that needs a fake LLM. `FakeAdapter` reads whatever schema it's called with and fills exactly its `required` fields, so one fake works for all three agents' different output contracts rather than needing a fake per agent.

## The pattern for testing a new agent

Monkeypatch `agent_platform.stages.pipeline_stages._build_adapter` (shared by every agent's `reason_llm` stage, regardless of which agent's pipeline calls it) to return a fake object implementing `generate_structured(*, system_prompt, user_prompt, schema, temperature) -> (dict, dict)`:

```python
from fakes import FakeAdapter  # tests/fakes.py — reads `schema["required"]` for you

@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())
```

Then call `invoke_agent("your_agent_id", {...})` directly and assert on the returned `RunContext` — `.decision`, `.rule_results`, `.error`, `.hitl`, `.explanation`. No server needs to be running; `tests/conftest.py` puts the repo root on `sys.path` so `agent_platform` and `capabilities_impl` import directly. Note `tests/` has no `__init__.py`, so import `fakes` (not `.fakes`) — pytest's rootless collection mode puts the `tests/` directory itself on `sys.path`.

## What isn't covered by the fast suite

A real call to Ollama — worth doing manually (or via a separate, explicitly-marked live test) before a demo, since it's the one thing that can't be faked: whether the model actually honors the JSON schema, how long a real call takes, and whether the tunnel is up. See `docs/running.md` for the CLI/API commands to do this by hand.

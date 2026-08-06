# The commercial_leadgen_demo workflow

Three agents composed into one deterministic business journey: discover a
lead, qualify it, and — only if it's actually eligible — generate a
proposal. Demonstrates that the runtime built for one agent scales to a
multi-agent workflow without a second orchestration engine.

```
START -> discover_leads -> qualify_lead -> qualification_router
  ├── NOT_QUALIFIED       -> stop, return discovery + qualification + reasons
  ├── NEEDS_HUMAN_REVIEW  -> stop, return discovery + qualification + pending_review
  └── QUALIFIED / CONDITIONALLY_QUALIFIED
         -> generate_proposal -> END
```

## How it's built (and why it's not a new engine)

`agent_platform/workflows/commercial_leadgen_demo.py` is a plain Python
function. It calls `invoke_agent("lead_discovery", ...)`,
`invoke_agent("lead_qualification", ...)`, and conditionally
`invoke_agent("proposal", ...)` — the exact same entry point the CLI and
FastAPI already use for a single agent. Branching is a plain `if`. Nothing
about this file is agent-specific runtime code; it's orchestration logic,
which genuinely is code, not YAML — unlike an agent, a workflow's behavior
*is* its branching, so config can't fully replace it here.

Each node (`validate_request`, `discover_leads`, `select_lead`,
`qualify_lead`, `qualification_router`, `generate_proposal`,
`finalize_response`) is wrapped in `run_node()`
(`agent_platform/workflows/executor.py`) — the same timing/logging/
StageResult bookkeeping `run_pipeline()` gives every agent stage, just
usable for heterogeneous nodes that can branch and call other agents. The
workflow's own execution is modeled as a `RunContext` (the same dataclass
every agent run uses), so `save_run`/`get_run`/`/runs/{run_id}` work on a
workflow run with zero new persistence code — `GET /runs/<run_id>` returns
the workflow's full record exactly like it would for any agent.

## The two new agents

### Lead Discovery (`agents/lead_discovery/`, `skills_library/lead_discovery/`)

Searches a fixture-backed SME pool (`capabilities_impl/lead_search.py`,
reusing the same `leads.json` Lead Qualification reads) by industry,
location, business need and minimum turnover. Ranking is deterministic —
`rank_leads` reuses `rules_engine.score_category` (the same weighted-band
engine Lead Qualification's category scores use) against
`rules/ranking_rules.yaml`'s four signals: turnover growth, business
vintage, active GST filing, and cash-flow health. The LLM only writes
`selection_reason` explaining the (already-decided) top pick — it cannot
change the selection.

### Proposal (`agents/proposal/`, `skills_library/proposal/`)

Takes a lead, a qualification result, and the list of products
qualification already deemed eligible — it never re-derives eligibility.
`select_products` reuses `rules_engine.evaluate_products` (same engine
Lead Qualification's product matching uses) against
`rules/product_fit_rules.yaml`, scoped to only the eligible product ids,
to rank among them and attach a deterministic `fit_score`. The LLM writes
`customer_proposal` and `next_best_action`; `required_documents` and
`key_benefits` come straight from the matched product's catalog entry,
never from the model.

Both agents reuse `load_input`, `reason_llm`, `validate_output`, and
`explain` from `pipeline_stages.py` completely unmodified — the only new
code is `discovery_stages.py` (`search_leads`, `rank_leads`) and
`proposal_stages.py` (`select_products`, `finalize_proposal`).

## What had to be generalized to make that reuse real

Reusing `reason_llm`/`validate_output`/`explain` across three differently-
shaped agents surfaced a few places where the original Lead Qualification
implementation was more specific than its docstrings claimed. Fixed,
additively, with the existing 27 tests as a regression gate before and
after each change:

- `prompt_assembler.build_citation_keys` hardcoded evidence sections as
  `("lead", "financials", "bureau", "kyc")` — now recursively flattens
  whatever evidence/facts dict it's given.
- `output_validator.REQUIRED_FIELDS` was a module-level constant — now
  reads from the calling skill's own `output_contract["required"]`, so
  Discovery's `{selection_reason, confidence}` contract doesn't get forced
  into Qualification's `{summary, strengths, risks, ...}` shape.
- `rules_engine.evaluate_products` only returned a fixed 4-key dict —
  now passes through every field a product entry declares, so Proposal's
  richer catalog (`key_benefits`, `required_documents`) survives.
- `decision_record.build` now also carries the raw `rule_results` through
  (additive key), so an agent whose facts don't fit the gates/scores/
  products shape (a ranking, a product-fit list) still gets a "Computed
  facts" section in the rendered explanation instead of nothing.

None of these changed Lead Qualification's behavior — same 27 tests, same
assertions, same values, before and after.

## Running it

```powershell
python cli.py list-workflows
python cli.py workflow commercial_leadgen_demo --input-file examples/discovery_request.json
```

Or over HTTP: `POST /workflows/commercial_leadgen_demo/invoke` with the
same JSON body, `GET /workflows` to list, `GET /runs/{run_id}` and
`GET /runs/{run_id}/explanation` work unchanged for a workflow run_id.

### The three demo scenarios

| request | discovered lead | qualification outcome | proposal |
|---|---|---|---|
| `{"industry": "manufacturing", "location": "Chennai", "business_need": "working_capital"}` | SME-1001 | QUALIFIED | generated |
| `{"industry": "construction", "location": "Chennai"}` | SME-1004 | NOT_QUALIFIED (sanctions hit) | skipped |
| `{"industry": "import_export", "location": "Chennai"}` | SME-1003 | NEEDS_HUMAN_REVIEW (bureau floor) | skipped |

All three branches are covered deterministically in
`tests/test_commercial_leadgen_workflow.py` with a fake LLM adapter
(`tests/fakes.py` — one fake that reads each skill's own output-contract
schema, so it works for all three agents without per-agent test fakes).

## A real operational finding from live testing

Live-verifying the third scenario (QUALIFIED -> proposal generated)
repeatedly hit the same failure mode: the shared demo Ollama tunnel 503s
the *second* back-to-back LLM call within one workflow run — not
instantly, but after a long queued delay (observed: ~30s per attempt
before rejecting). This is the remote server being resource-constrained
under sequential load, not a bug here — confirmed by testing each agent
individually against live Ollama successfully, and the branching logic
itself is proven correct deterministically.

`OllamaAdapter` now retries transient failures (connection errors,
502/503/504) up to 3 times with a 5s backoff before giving up
(`agent_platform/llm/ollama_adapter.py`) — a genuine, agent-agnostic
robustness improvement motivated directly by this. When retries are
exhausted, the existing graceful-degradation path takes over: deterministic
fallback rationale, confidence drops, `hitl_gate` correctly escalates to
`NEEDS_HUMAN_REVIEW` — which is itself the correct, safe behavior under a
flaky LLM, not a failure of the system. If a live demo needs the QUALIFIED
path specifically, run it in isolation (not immediately after another
agent's LLM call) or expect an occasional escalation to human review
instead of a proposal — and note that this is the system doing exactly
what it's designed to do when the model is unreliable.

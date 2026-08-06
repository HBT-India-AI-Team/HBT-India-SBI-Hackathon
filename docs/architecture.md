# Architecture

## The one rule everything else follows

**The runtime contains zero references to "lead", "qualification", "proposal", or any banking concept.** Everything domain-specific is data (YAML/Markdown/JSON), loaded by a generic loader and executed by a generic pipeline. Adding a new agent means adding a new `agents/<id>/agent.yaml` and a new `skills_library/<id>/` directory — not new runtime code.

This maps directly onto the platform diagram's layers:

| Diagram layer | Where it lives in this repo |
|---|---|
| Agent Definition | `agents/<id>/agent.yaml` |
| Skill Package | `skills_library/<id>/` (instructions, output contract, rules) |
| Capability Package | `agent.yaml`'s `capabilities:` block, resolved against `agent_platform/capabilities/` |
| Governance & Controls | `agent.yaml`'s `governance:` block |
| Agent Composer | `agent_platform/composition/loader.py` — one function, `load_agent()` |
| Shared Runtime | `agent_platform/runtime/` — `RunContext` + the pipeline executor |
| MCP / A2A | `agent_platform/capabilities/future.py` — documented `Protocol` stubs, not implemented |

## Directory layout

```
banking-agent/
  agent_platform/            # the reusable runtime — no agent-specific code
    composition/              # config models + AgentLoader (load_agent, list_agents)
    runtime/                   # RunContext, StageResult, pipeline executor
    stages/                     # reusable lifecycle stage functions
    skills/                      # generic rules engine, prompt assembler, output validator
    capabilities/                 # Tool protocol + registry; MCP/A2A placeholders
    llm/                           # LLMProvider protocol + OllamaAdapter
    observability/                  # structured JSONL logging
    explainability/                   # DecisionRecord builder + Markdown renderer
    state/                              # run persistence (one JSON per run)
    workflows/                           # multi-agent orchestration (see below)

  skills_library/             # THE BANKING SKILL LIBRARY — YAML + Markdown, no code
    shared/                     # guardrails included by every skill
    lead_qualification/          # gates, weighted scores, product-fit rules
    lead_discovery/                # search/ranking rules
    proposal/                        # product-fit-among-eligible rules
    echo_probe/                        # minimal skill for the reusability proof

  agents/                      # AGENT DEFINITIONS — pure config, no code
    lead_qualification/agent.yaml
    lead_discovery/agent.yaml
    proposal/agent.yaml
    echo_probe/agent.yaml

  capabilities_impl/            # mock implementations of external systems
    lead_data.py, credit_bureau.py, kyc.py, lead_search.py
    fixtures/                     # JSON standing in for a CRM / bureau / KYC system

  backend/main.py                # FastAPI surface — thin, calls invoke_agent()/run_workflow()
  cli.py                          # CLI surface — thin, calls invoke_agent()/run_workflow()
  tests/
  runs/                            # one JSON per run (gitignored in spirit, not tracked)
  logs/                             # structured JSONL event log
```

## Request flow (one agent invocation)

```
CLI or FastAPI
  -> agent_platform.runtime.executor.invoke_agent(agent_id, raw_input)
       -> composition.load_agent(agent_id)          # reads agent.yaml + skill package once, caches it
       -> RunContext.start(...)                       # one run_id, one correlation_id
       -> runtime.pipeline.run_pipeline(bundle, ctx, logger)
            for stage_name in bundle.definition.pipeline:
                STAGE_REGISTRY[stage_name](ctx, bundle, logger)   # mutates ctx in place
       -> decision_record.build(ctx, bundle)          # always runs, even after a stage fails
       -> state.save_run(ctx)                          # always runs
  <- RunContext (decision, explanation, error, stage_results)
```

Every stage is `(ctx, bundle, logger) -> None`. The executor has no idea what any stage does; it just times it, logs it, catches exceptions, and appends a `StageResult`. This is why a completely different agent (see `echo_probe`, a 2-stage pipeline with no LLM call at all) runs through the *exact same* `run_pipeline()` with zero code changes.

## The Lead Qualification Agent's pipeline

```
load_input        -- generic: checks agent.yaml's input_schema.required against raw_input
gather_evidence    -- looks up lead/bureau/KYC via the capability registry
evaluate_rules      -- runs the skill's YAML rules: hard gates, weighted scores, product matches
reason_llm            -- asks Ollama to narrate over the rule results (never to decide)
validate_output        -- strips any decision-like field the LLM tried to add; checks every
                           citation against the real evidence; retries once on structural failure
decide                    -- THE decision, computed from gates + composite score, not from the LLM
hitl_gate                  -- may escalate to NEEDS_HUMAN_REVIEW based on governance rules
explain                      -- builds the DecisionRecord (the full "why" artifact)
```

**Deterministic-first, LLM-second.** The rules engine (`agent_platform/skills/rules_engine.py`) computes every gate, score, and product match from YAML config. The LLM only ever narrates over facts it's handed and cites; `output_validator.py` enforces that it cannot smuggle a decision-shaped field into its output, and that every claim it makes traces back to a real evidence key. If the LLM is unavailable, `decide`/`hitl_gate` still run correctly off the rules engine alone — the run degrades to a deterministic fallback rationale, not a failure.

## Multi-agent workflows

`agent_platform/workflows/` composes several agents into one deterministic business journey, or picks between them, without adding a second orchestration engine. Two workflows exist today:

- **`commercial_leadgen_demo`** (see `docs/commercial-leadgen-workflow.md`) calls Lead Discovery, then Lead Qualification, then conditionally Proposal, purely by their registered `agent_id` via `invoke_agent()`.
- **`agent_router`** (see `docs/agent-router.md`) goes the other direction: given a raw request and no known `agent_id`, an LLM picks which single registered agent should handle it (or a caller can pass `agent_id` directly to skip that decision). Every agent contributes itself to the router's catalog automatically via its `agent.yaml`'s `purpose` field and a `routable: bool` flag (default `true`) — adding or excluding an agent from routing is a YAML change, not a router code change.

A workflow is a plain Python function, not another declarative-config layer: branching (or LLM-based selection) genuinely is code, unlike an agent's behavior which is fully described by YAML. Both still reuse the platform everywhere they can — `run_node()` gives workflow steps the same timing/logging bookkeeping `run_pipeline()` gives agent stages, and a workflow's own execution is modeled as a `RunContext`, so it's persisted and queryable through `/runs/{run_id}` with zero new code.

## Why this design is genuinely reusable, not just described that way

- `rules_engine.py` has no concept of "SME" or "bureau" — it operates on dotted field paths, banded thresholds, and `when` conditions declared in YAML. Any skill can define its own gates/factors/composite/product-matching using the same engine.
- `prompt_assembler.py` builds citation keys generically from whatever evidence dict a stage hands it, not from a hardcoded list of section names.
- `AgentLogger` is duck-typed (`getattr(ctx, "run_id", None)`) — it works with any object that looks like a `RunContext`, not just the agent one.
- `state/run_store.py`'s `save_run`/`get_run` operate on the same duck-typed shape, so anything that produces a `RunContext`-compatible object — including a future multi-agent workflow — is inspectable through the exact same `/runs/{run_id}` endpoint with no new persistence code.

## What's deliberately not built

- **MCP / A2A**: `agent_platform/capabilities/future.py` has `Protocol` interfaces only — no client, no server, no network code. When a real MCP server exists, implement the protocol and swap the registration in `capabilities_impl/__init__.py`; no stage or runtime code changes.
- **Checkpointing / replay**: a run is persisted once, after it finishes. There's no resume-from-mid-pipeline. `runs/<run_id>.json` records exactly: run_id, input summary, stage results, final result, explanation, error.
- **Real enterprise integrations**: `capabilities_impl/` is mock data (JSON fixtures) standing in for a lead CRM, a credit bureau API, and a KYC/sanctions system.

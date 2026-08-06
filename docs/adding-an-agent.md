# Adding a new agent

This is the whole point of the runtime: a new agent should be config, not code. This doc walks through the mechanism using the two agents that already exist as reference — `lead_qualification` (full pipeline, real capabilities, LLM narrative) and `echo_probe` (minimal — exists purely to prove this mechanism works).

## 1. Write the skill package

`skills_library/<skill_id>/`:

- `skill.yaml` — manifest: `skill_id`, `version`, `description`, and paths to the files below
- `instructions.md` — the system prompt for the LLM stage (persona, task, constraints)
- `output_contract.json` — a JSON Schema the LLM's output is constrained to (Ollama's structured-output `format`)
- `rules/*.yaml` — whatever deterministic logic your agent needs, referenced from `skill.yaml`'s `rules:` map

`skill.yaml` can also list `shared_includes:` (e.g. `shared/compliance_guardrails.md`) to pull in text every skill should share.

If your agent has no LLM step and no rules (like `echo_probe`), `output_contract.json` and `rules:` can be minimal/empty — `SkillPackage` still requires the files to exist, just not to contain much.

## 2. Write the agent definition

`agents/<agent_id>/agent.yaml`:

```yaml
agent_id: your_agent_id
version: "1.0.0"
purpose: >
  One paragraph, human-readable.

skill: your_skill_id          # must match skill_id in skill.yaml

pipeline:                      # ordered list of stage NAMES — see step 3
  - load_input
  - ...
  - explain

capabilities:                  # tools this agent may call (see step 4)
  - name: some_capability.some_function
    kind: tool

governance:
  hitl_conditions: []           # which conditions can escalate to human review
  confidence_threshold: 0.6
  max_llm_retries: 1

llm:
  model: gemma4:12b              # per-agent — different agents can use different models
  temperature: 0.0
  seed: 7
  timeout_seconds: 120

input_schema:
  type: object
  required: [your_required_field]   # load_input (generic, already exists) enforces this
  properties:
    your_required_field: {type: string}

routable: true                 # optional, defaults to true — set false if this agent
                                # shouldn't be picked by agent_router (docs/agent-router.md),
                                # e.g. a proof-of-concept agent like echo_probe
```

That's it — no Python class to register the agent. `AgentLoader.load_agent(agent_id)` finds it by scanning `agents/*/agent.yaml`. Leaving `routable` at its default means your new agent is automatically selectable by `agent_router` the moment it's added — write a clear `purpose:`, since that's the only thing the router's LLM sees to decide when your agent is the right fit.

## 3. Reuse existing stages, write new ones only where genuinely new behavior is needed

Stages already registered and reusable as-is by any agent:

| stage | what it does | reusable when |
|---|---|---|
| `load_input` | checks `input_schema.required` against the raw input | always — fully generic |
| `reason_llm` | builds a prompt from evidence + facts, calls Ollama with structured output | your agent has an `evidence` dict and wants LLM narrative over it |
| `validate_output` | strips forbidden fields, drops ungrounded citations, retries once, falls back to a deterministic rationale on failure | pairs with `reason_llm` |
| `explain` | builds the DecisionRecord from whatever's on `ctx` | always — fully generic |

If your agent needs to fetch or compute something specific (a new kind of lookup, a new kind of scoring), write a new stage function in `agent_platform/stages/` and register it:

```python
from agent_platform.runtime.pipeline import register_stage

@register_stage("your_new_stage")
def your_new_stage(ctx, bundle, logger) -> None:
    ...  # read/write ctx.evidence, ctx.rule_results, ctx.decision, etc.
```

Then reference `"your_new_stage"` by name in your `agent.yaml`'s `pipeline:` list. This is the *only* code most new agents should need — everything else (loading, logging, persistence, the LLM call, explanation) is already generic.

If your deterministic logic is gates/weighted-scores/pattern-matching, reach for `agent_platform/skills/rules_engine.py` first (`evaluate_gates`, `score_category`, `compute_composite`, `evaluate_products`) instead of writing bespoke logic — it's already generic over any field names, weights, and thresholds you put in YAML.

## 4. Register any new capabilities

If your agent needs to call something (a lookup, an API, a mock fixture), add a function to `capabilities_impl/` and register it in `capabilities_impl/__init__.py`:

```python
DEFAULT_REGISTRY.register("your_capability.your_function", your_function, "description")
```

Stages call it by name: `DEFAULT_REGISTRY.invoke("your_capability.your_function", **kwargs)`. This is the seam where a mock becomes a real MCP-backed tool later — swap what's registered under the same name, no stage code changes.

**Watch for shared mutable state**: if your capability returns data from an in-memory cache (like the fixture-backed ones do), return a deep copy, not a live reference — a caller mutating what you handed back will corrupt your cache for every future call. (`capabilities_impl/lead_data.py` does this correctly; it's also why `gather_evidence` builds a new dict instead of `.pop()`-ing fields off what a capability returns.)

## 5. Verify

```powershell
python cli.py list-agents          # your new agent_id should appear
python cli.py run your_agent_id --lead-id ...   # or whatever your input_schema requires
```

Add a test in `tests/` — see `docs/testing.md` for the pattern (monkeypatch the LLM adapter so tests don't need live Ollama).

## What you should never need to touch

`agent_platform/runtime/`, `agent_platform/composition/`, `backend/main.py`'s routes, `cli.py`'s commands. If adding an agent seems to require changing one of these, that's a sign the runtime has a gap worth generalizing — not a reason to special-case your agent inside it.

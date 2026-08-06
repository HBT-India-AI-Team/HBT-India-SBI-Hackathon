# The agent_router workflow

Lets a caller hit one generic entry point without knowing our agent_id
catalog — either give it a raw request and an LLM decides which registered
agent should handle it, or pass `agent_id` directly to skip the LLM
entirely and invoke that agent yourself. Both "let the platform decide"
and "I already know which agent I want" are first-class, same as they were
before this existed:

```
POST /agents/{agent_id}/invoke        -- unchanged, bypasses the router entirely
POST /workflows/agent_router/invoke   -- new, single entry point for either mode
```

## How it decides

`agent_platform/workflows/agent_router.py` is a plain Python workflow
(`@register_workflow("agent_router")`), structurally the same shape as
`commercial_leadgen_demo.py`'s branching — a decision point that picks
which agent runs next, generalized from "pick a branch" to "pick an
agent." No new runtime/engine code was needed.

```
START -> build_catalog -> select_agent (LLM, or explicit agent_id override)
  ├── no confident choice -> NEEDS_CLARIFICATION, no agent invoked
  └── confident choice    -> invoke that agent -> COMPLETED / AGENT_FAILED
```

1. **`build_catalog`** — every registered agent whose `agent.yaml` has
   `routable: true` (the default — you only need to set `routable: false`
   for an agent that shouldn't be auto-selected, like `echo_probe`, which
   exists purely to prove runtime reusability, not to handle real
   requests) contributes its `agent_id` and `purpose` string to the
   catalog. Add a new agent and it becomes routable automatically — zero
   router code changes.
2. **`select_agent`** — if the request body includes `agent_id` directly,
   that's used as-is (validated against the catalog, no LLM call made at
   all). Otherwise an LLM (`gemma4:12b` over the same `OLLAMA_HOST` tunnel
   every agent uses) is shown each candidate agent's `purpose` and asked
   to pick one, constrained via JSON schema (`agent_id` is an `enum` of
   the real catalog ids, plus `confidence` and `reasoning`).
3. **Two checks before trusting the LLM's pick** — this platform never
   trusts raw LLM output blindly (`output_validator.py` exists for
   exactly this reason): the chosen `agent_id` must actually be in the
   catalog, and `confidence` must be at least `0.6` (matching the same
   threshold `lead_qualification`'s own HITL gate uses). Failing either
   check, or the routing LLM call failing outright (even after its
   built-in retries) → the router does **not** guess — it returns
   `NEEDS_CLARIFICATION` with the reasoning, and no agent is invoked.
4. On a confident pick, the chosen agent is invoked exactly the way the
   CLI/API always have (`invoke_agent(agent_id, raw_input)`). Because
   `invoke_agent` never raises on a downstream failure (a bad stage just
   sets `.error` on its `RunContext` and returns normally), the router
   explicitly checks `agent_ctx.error` afterward and reports
   `AGENT_FAILED` rather than a false `COMPLETED` — this matters if the
   LLM confidently routes to an agent whose `input_schema` the request
   doesn't actually satisfy.

## Response shape

```json
{
  "workflow_id": "agent_router",
  "run_id": "run_...",
  "status": "COMPLETED",
  "chosen_agent_id": "lead_qualification",
  "routing_confidence": 0.92,
  "routing_reasoning": "...",
  "agent_run_id": "run_...",
  "decision": { "outcome": "QUALIFIED", "...": "..." },
  "explanation": { "...": "..." }
}
```

`status` is one of:

| status | meaning |
|---|---|
| `COMPLETED` | routed and the chosen agent ran successfully |
| `NEEDS_CLARIFICATION` | no confident/valid agent choice — nothing was invoked |
| `AGENT_FAILED` | routing succeeded but the chosen agent's own run errored |
| `FAILED` | unexpected router-level error (e.g. no routable agents registered) |

Always HTTP 200 — same as `commercial_leadgen_demo`, which never raises
past `run_workflow`; failure is a status in the body, not an HTTP error.

## Examples

```powershell
# let it decide
curl.exe -X POST http://127.0.0.1:8080/workflows/agent_router/invoke `
  -H "Content-Type: application/json" `
  -d '{\"lead_id\":\"SME-1001\"}'

# tell it directly (skips the LLM call entirely)
curl.exe -X POST http://127.0.0.1:8080/workflows/agent_router/invoke `
  -H "Content-Type: application/json" `
  -d '{\"agent_id\":\"lead_qualification\",\"lead_id\":\"SME-1001\"}'
```

```powershell
python cli.py workflow agent_router --input '{"lead_id": "SME-1001"}'
```

The run is persisted and queryable exactly like any other agent or
workflow run: `GET /runs/{run_id}`, `GET /runs/{run_id}/explanation`.

## What this doesn't do

No rule-based routing layer — routing is LLM-first with the
confidence-threshold/catalog checks above as the safety net, not a
deterministic keyword matcher. No new auth or deployment — this runs
locally like everything else; exposing it to other platforms is a
separate, later piece of work.

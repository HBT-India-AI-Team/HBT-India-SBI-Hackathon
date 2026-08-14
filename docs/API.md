# API — every endpoint this backend exposes

**Generated from `backend.main:app` — do not edit by hand.**
Run `python scripts/dump_api_surface.py` after changing any route.

This exists to be diffed against what a client is actually calling. A
client team was hitting us with a field name we did not read and a flag
nested a level below where we looked — both invisible from either side.

For *who* calls what, and the request shapes they send, see
[INTEGRATIONS.md](INTEGRATIONS.md). This file is only what exists.

`key` = requires the agent's `X-API-Key` header.

---

## For client integrations

These are the only ones an outside team should be calling.

| method | path | key | what |
|---|---|---|---|
| `GET` | `/agents/{agent_id}` | — | Get Agent |
| `POST` | `/agents/{agent_id}/chat` | yes | The public, key-gated counterpart to /invoke: same auth, but takes free-text ({"session_id": str \| null, "message": str}) instead |
| `POST` | `/agents/{agent_id}/invoke` | yes | Body is the agent's raw input as-is (e.g |
| `POST` | `/agents/{agent_id}/invoke/stream` | yes | Streaming counterpart to /invoke: same body, same key, sentences first |
| `GET` | `/embed/{agent_id}` | — | What a client's site iframes in |
| `GET` | `/healthz` | — | Healthz |

---

## Internal — the Playground UI

No API key. Same trust level as a local dev tool; the frontend is the
only thing that should touch these.

| method | path | key | what |
|---|---|---|---|
| `GET`, `POST` | `/admin/agents` | — | Get Agents Summary |
| `POST` | `/admin/agents/generate` | — | Describe-it flow: an LLM authors a real spec (gates/factors/ thresholds/products, or input/output fields + guidance, depending on |
| `POST` | `/admin/agents/generate/stream` | — | Same as POST /agents/generate, but streamed over SSE so the client can show real per-step progress instead of a single opaque spin |
| `DELETE` | `/admin/agents/{agent_id}` | — | Removes agents/<agent_id>/ only |
| `GET` | `/admin/agents/{agent_id}/api-key` | — | Returns the key a client uses to call POST /agents/{agent_id}/invoke (the public, non-admin endpoint) |
| `POST` | `/admin/agents/{agent_id}/api-key/regenerate` | — | Invalidates the old key immediately |
| `POST` | `/admin/agents/{agent_id}/chat` | — | No API key required — this is the internal Playground's chat mode, same trust level as test-run above |
| `POST` | `/admin/agents/{agent_id}/chat/stream` | — | Streaming counterpart to /chat, for the Playground |
| `POST` | `/admin/agents/{agent_id}/edit-file` | — | The one "Fix with AI" mechanism — edits exactly the file you have open, the way a careful human applies a targeted change: the mod |
| `GET`, `PUT` | `/admin/agents/{agent_id}/files` | — | Get Agent Files |
| `POST` | `/admin/agents/{agent_id}/input-mode` | — | Lets Playground change which interface an agent defaults to without anyone hand-editing agent.yaml |
| `POST` | `/admin/agents/{agent_id}/refine` | — | Human-in-the-loop correction for a draft agent: describe what's wrong in plain language, the LLM corrects the existing rules (give |
| `POST` | `/admin/agents/{agent_id}/refine/stream` | — | Same as POST /agents/{agent_id}/refine, but streamed over SSE — see _refine_agent_events |
| `POST` | `/admin/agents/{agent_id}/skills` | — | Adds a skill to agent.yaml's `skills:` list — load_skills can load it alongside any of the agent's other skills |
| `DELETE` | `/admin/agents/{agent_id}/skills/{skill_id}` | — | Detaches skill_id from agent.yaml's `skills:` list |
| `POST` | `/admin/agents/{agent_id}/test-run` | — | Test Run Agent |
| `POST` | `/admin/agents/{agent_id}/test-run-file` | — | The file-upload counterpart to /test-run — for input_mode: "file" agents (currently just fin_health) |
| `GET` | `/admin/api-surface` | — | Every endpoint this backend serves, plus what has actually been called |
| `GET` | `/admin/archetypes` | — | Agent shapes the "describe it" AI generator can produce |
| `GET` | `/admin/capabilities` | — | Get Capabilities |
| `POST` | `/admin/explain/markdown` | — | Renders a decision_record.build()-shaped `explanation` (whatever a test-run/agent_router response already carries) into a single r |
| `GET` | `/admin/ollama-logs` | — | Every Ollama call attempt (including failed retries), most recent first — the Logs page's data source |
| `GET` | `/admin/ollama-logs/{offset}` | — | The full record |
| `GET` | `/admin/skills` | — | Every skills_library/<id> directory, kind inferred structurally (presence of `output_contract` in its manifest, same as the loader |
| `GET` | `/admin/stages` | — | Every stage name a pipeline can reference today |
| `GET` | `/admin/templates` | — | Starter shapes the New Agent flow can scaffold from. |

---

## Everything else

| method | path | key | what |
|---|---|---|---|
| `GET` | `/agents` | — | Get Agents |
| `GET` | `/api/tools` | — | Every calculator, with its input fields |
| `POST` | `/api/tools/execute` | — | Compute a result |
| `POST` | `/api/tools/save` | — | Remember a user's inputs for a calculator, so it comes back filled in. |
| `GET` | `/api/tools/saved` | — | A user's saved calculators |
| `GET` | `/runs` | — | Get Runs |
| `GET` | `/runs/{run_id}` | — | Get Run Detail |
| `GET` | `/runs/{run_id}/explanation` | — | Get Run Explanation |
| `GET` | `/workflows` | — | Get Workflows |
| `POST` | `/workflows/{workflow_id}/invoke` | — | Invoke Workflow |

---

## Checking what a client is really hitting

Uvicorn logs every request. A wrong path shows up as a 404 against a
path that is not in the tables above:

```bash
# what has been called, most-hit first
grep -o '"[A-Z]* /[^ ]*' uvicorn_out.log | sort | uniq -c | sort -rn

# only the failures — a 404 here is usually a wrong path or a typo'd agent_id
grep -E '" (4|5)[0-9][0-9] ' uvicorn_out.log
```

A request that reaches the right path but carries the wrong *field names*
will show as `200 OK` here and still do nothing useful. That class of
failure is only visible in `logs/ollama_calls.jsonl`, which holds the full
prompt actually sent to the model.

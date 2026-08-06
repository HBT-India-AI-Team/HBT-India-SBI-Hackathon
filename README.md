# Reusable Agentic AI Platform — Lead Discovery, Qualification & Proposal

A production-quality demonstration of a shared, skill-driven agent runtime carrying three composed agents into one business workflow: adding the next agent means adding config (an `agent.yaml` + a skill package), not new runtime code. See `docs/architecture.md` for the proof, not just the claim.

**Status:** Lead Qualification Agent — complete, verified against a live Ollama model. Lead Discovery and Proposal agents built on the same runtime and composed into the `commercial_leadgen_demo` workflow (discover → qualify → propose, with branching on the qualification outcome). A fourth agent (`echo_probe`) exists purely to prove the runtime is reusable, not just described that way.

## Quick start

```powershell
.\venv\Scripts\Activate.ps1
python cli.py list-agents
python cli.py run lead_qualification --lead-id SME-1001

python cli.py list-workflows
python cli.py workflow commercial_leadgen_demo --input-file examples/discovery_request.json
```

Or the API:

```powershell
python -m uvicorn backend.main:app --reload --port 8080
# then http://127.0.0.1:8080/docs
```

Or the agent/skill editor UI (build once, then it's served from the same command above):

```powershell
cd frontend && npm install && npm run build && cd ..
python -m uvicorn backend.main:app --reload --port 8080
# then http://127.0.0.1:8080
```

## Docs

| doc | what's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | the runtime design, directory layout, request flow, why it's genuinely reusable |
| [`docs/running.md`](docs/running.md) | CLI/API commands, Ollama config, sample leads and expected outcomes |
| [`docs/lead-qualification-agent.md`](docs/lead-qualification-agent.md) | the qualification agent's gates/scoring/product-matching rules, in detail |
| [`docs/commercial-leadgen-workflow.md`](docs/commercial-leadgen-workflow.md) | the 3-agent workflow, the two new agents, and what had to be generalized to make the reuse real |
| [`docs/agent-router.md`](docs/agent-router.md) | letting an LLM pick which agent handles a request, or picking one yourself in the same call |
| [`docs/editor-ui.md`](docs/editor-ui.md) | the React agent/skill editor — what it can and can't build, how it's wired to the runtime |
| [`docs/adding-an-agent.md`](docs/adding-an-agent.md) | step-by-step: how to add the next agent (by hand, or via the editor UI) |
| [`docs/testing.md`](docs/testing.md) | what the tests cover and the pattern for adding more |

## What's built

- **Skill-Driven Agent Runtime** (`agent_platform/`) — config loader, pipeline executor, generic rules engine, Ollama adapter, structured logging, explainability, run persistence, workflow orchestration. No business-domain references anywhere in it.
- **Banking Skill Library** (`skills_library/`) — Lead Qualification (SME gates, weighted scoring, product-fit rules), Lead Discovery (search/ranking rules), Proposal (product-fit-among-eligible rules), plus shared compliance guardrails every skill includes.
- **Three agents** (`agents/`) — pure config, no code, resolved by the loader at call time.
- **Two workflows** (`agent_platform/workflows/`) — `commercial_leadgen_demo.py` composes the three agents by `agent_id`, branches on the qualification outcome; `agent_router.py` lets an LLM (or an explicit `agent_id` override) pick which single agent should handle a request, so callers don't need to know the agent catalog. Both persisted and queryable through the same `/runs/{run_id}` every agent run uses.
- **Mock capabilities** (`capabilities_impl/`) — lead search/lookup, bureau, KYC, backed by 5 sample SME leads covering every decision outcome.
- **CLI and FastAPI surfaces** — both thin, both calling the same `invoke_agent()` / `run_workflow()`.
- **Agent & Skill Editor UI** (`frontend/`, React + TypeScript + Tailwind) — create a new agent, edit any agent's config/rules/instructions with validation, test-run it — all through the same `backend/admin.py` API, which reuses the exact same platform code (no parallel validation logic).
- **55 tests** (+1 intentionally skipped live-only test), all fast and offline (LLM calls are faked via a schema-driven fake, not skipped).

## What's explicitly not built

MCP/A2A (documented `Protocol` stubs only), checkpoint/replay, real CRM/bureau/KYC/GST/MCA integrations, authentication, the other agents in the platform diagram (SBI Brain, Twin Builder, Knowledge Graph, etc.) — see `docs/architecture.md` for the full list and why.

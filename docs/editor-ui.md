# Agent & Skill Editor UI

A React + TypeScript + Tailwind frontend (`frontend/`) for creating and editing agents without touching the filesystem directly — the concrete answer to "will there be a UI to build new agents."

## What it does

- **Lists every registered agent** (reads the same `agent_platform.composition.list_agents()` the CLI uses)
- **Creates a new agent + skill scaffold** from a name and a one-line purpose — generates a starter `agent.yaml` (pipeline: `load_input → reason_llm → validate_output → explain`) and a starter skill package (`skill.yaml`, `instructions.md`, `output_contract.json`)
- **Edits every file a skill package is made of** — `agent.yaml`, `skill.yaml`, `instructions.md`, `task_prompt.md`, `output_contract.json`, and every `rules/*.yaml` file, each in its own syntax-highlighted CodeMirror tab
- **Validates before saving** — YAML/JSON syntax, then the exact same `AgentDefinition`/`SkillPackage` Pydantic models the runtime uses, then a full `load_agent(force_reload=True)` to catch cross-file problems (e.g. a rule file referenced from `skill.yaml` that doesn't parse)
- **Test-runs the agent** — a JSON input box and a Run button that calls the real `invoke_agent()`, showing the decision and full explanation, exactly like the CLI/API would (this makes a real LLM call, same latency as everywhere else)
- **A reference panel** listing every currently-registered pipeline stage and capability, so someone building a new agent knows what's available without reading source code

## What it deliberately does not do

It cannot add new stage code (a new kind of data lookup or algorithm) — that's still `agent_platform/stages/*.py`, written by a developer, same as Discovery's `search_leads`/`rank_leads` or Proposal's `select_products` were. This UI covers agents built from **existing generic stages plus new rules/instructions**, which is most banking use cases (anything shaped like gates + weighted scores + an LLM narrating over the result). Said plainly in `docs/commercial-leadgen-workflow.md`'s and earlier conversation's framing: "build agent #4, #5, #6 that look like our existing ones" — not zero engineering ever again.

## Architecture

- **`backend/admin.py`** — a FastAPI router (`/admin/*`) mounted into the existing `backend/main.py`. Every endpoint reuses existing platform code (`load_agent`, `AgentDefinition`, `STAGE_REGISTRY`, `invoke_agent`) rather than reimplementing validation — "valid" here means exactly what it means everywhere else in the platform.
- **`frontend/`** — Vite + React + TypeScript + Tailwind CSS v4, no other framework. CodeMirror (`@uiw/react-codemirror`) provides YAML/JSON/Markdown syntax highlighting.
- The frontend calls the backend via **relative paths** everywhere (`fetch("/admin/agents")`, never a hardcoded host) — that's what lets the exact same built JS work in both dev (proxied) and production (same-origin), with zero environment-specific code.

## Running it

**Development** (hot reload, two terminals):

```powershell
# terminal 1 — backend
python -m uvicorn backend.main:app --reload --port 8080
# or: .\run_backend.ps1

# terminal 2 — frontend
cd frontend
npm install    # first time only
npm run dev
```

Open the URL Vite prints (usually **http://localhost:5173**, or the next free port if that's taken). `frontend/vite.config.ts` proxies `/admin`, `/agents`, `/workflows`, `/runs` to `http://127.0.0.1:8080` — the backend also has CORS configured for `localhost:5173`/`5174` as a fallback. If your backend runs on a different port (port `8000` hits a `WinError 10013` socket permission error on some Windows machines — pick any free port instead), update both the proxy targets in `vite.config.ts` and the CORS origins in `backend/main.py` to match.

**Production / demo** (one server, one URL):

```powershell
cd frontend
npm run build      # outputs frontend/dist/
cd ..
python -m uvicorn backend.main:app --reload --port 8080
```

Open **http://127.0.0.1:8080** — `backend/main.py` mounts `frontend/dist/` as static files (registered last, so it never shadows the API routes above it). Rebuild (`npm run build`) after any frontend change; the backend needs no changes to pick it up.

## Admin API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/agents` | agent summaries (id, version, purpose, skill, pipeline) |
| GET | `/admin/agents/{agent_id}/files` | every editable file's raw text for one agent |
| PUT | `/admin/agents/{agent_id}/files` | validate + save all files for one agent |
| POST | `/admin/agents` | scaffold a new agent + skill directory pair |
| POST | `/admin/agents/{agent_id}/test-run` | invoke the agent with given input, return decision + explanation |
| GET | `/admin/stages` | every pipeline stage name currently registered |
| GET | `/admin/capabilities` | every capability name + description currently registered |

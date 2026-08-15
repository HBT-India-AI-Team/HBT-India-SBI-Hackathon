# Running the platform

All commands assume you're in `banking-agent/` with the project venv.

```powershell
.\venv\Scripts\Activate.ps1
```

(Or skip activation and prefix every command with `.\venv\Scripts\python.exe` instead of `python`.)

## Ollama

The agent calls Ollama's `/api/chat` with a JSON schema for structured output. Configured via `backend/.env`:

```
OLLAMA_HOST=https://dreamboat-bleep-childhood.ngrok-free.dev/ollama
```

This is the only environment variable actually read by the code (`agent_platform/stages/pipeline_stages.py`). Model, temperature, seed and timeout are per-agent config in `agents/<id>/agent.yaml`'s `llm:` block — not environment variables — because different agents may reasonably want different models.

Lead Qualification currently uses `gemma4:12b` over the tunnel. If the tunnel is down, the agent doesn't fail — `reason_llm` catches the error, `validate_output` falls back to a deterministic rationale, and `hitl_gate` typically escalates the outcome to `NEEDS_HUMAN_REVIEW` because of the lowered confidence. This is tested (`test_llm_outage_falls_back_to_deterministic_rationale`). `OllamaAdapter` also retries transient failures (connection errors, 502/503/504) up to 3 times with a 5s backoff before giving up — the shared demo tunnel has been observed to occasionally 503 a second request arriving right after a first one completes; see `docs/commercial-leadgen-workflow.md` for what was found running this live.

## CLI

```powershell
# list every agent / workflow the loader can discover
python cli.py list-agents
python cli.py list-workflows

# invoke an agent — --lead-id is shorthand; other agents need --input or --input-file
python cli.py run lead_qualification --lead-id SME-1001
python cli.py run lead_discovery --input '{"industry":"manufacturing","location":"Chennai"}'
python cli.py run proposal --input-file examples/proposal_request.json

# invoke the full 3-agent workflow
python cli.py workflow commercial_leadgen_demo --input-file examples/discovery_request.json

# let an LLM pick which agent should handle a request (or pass agent_id yourself to skip that)
python cli.py workflow agent_router --input '{"lead_id": "SME-1001"}'
python cli.py workflow agent_router --input '{"agent_id": "lead_qualification", "lead_id": "SME-1001"}'

# re-view a past run (agent or workflow — same command either way)
python cli.py show-run <run_id>
python cli.py show-run <run_id> --json
```

Sample leads and their expected Lead Qualification outcomes (`capabilities_impl/fixtures/leads.json` + `bureau.json` + `kyc.json`):

| lead_id | outcome | why |
|---|---|---|
| `SME-1001` | QUALIFIED | strong financials, clean bureau/KYC |
| `SME-1002` | CONDITIONALLY_QUALIFIED | composite score between thresholds |
| `SME-1003` | NEEDS_HUMAN_REVIEW | bureau score below the automated floor (soft gate) |
| `SME-1004` | NOT_QUALIFIED | sanctions hit (hard gate) |
| `SME-1005` | NOT_QUALIFIED | KYC pending + business vintage too low (multiple hard gates) |

For the full discover -> qualify -> propose workflow and its three demo scenarios, see `docs/commercial-leadgen-workflow.md`. For letting the platform pick which agent handles a request (or picking it yourself in the same call), see `docs/agent-router.md`.

## FastAPI

```powershell
python -m uvicorn backend.main:app --reload --port 8080
# or: .\run_backend.ps1
```

Port `8000` is blocked by a Windows socket permission error (`WinError 10013`) on some machines — usually a Hyper-V/WSL reserved port range. `8080` is the documented default here for that reason; use whichever port is free on yours.

Interactive docs: **http://127.0.0.1:8080/docs**

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness check |
| GET | `/agents` | list registered agent ids |
| GET | `/agents/{agent_id}` | an agent's definition (pipeline, capabilities, governance) |
| POST | `/agents/{agent_id}/invoke` | run an agent — body is that agent's raw input, e.g. `{"lead_id": "SME-1001"}` |
| GET | `/workflows` | list registered workflow ids |
| POST | `/workflows/{workflow_id}/invoke` | run a workflow — body is its raw input |
| GET | `/runs?limit=50` | recent run summaries (agents and workflows) |
| GET | `/runs/{run_id}` | full run record |
| GET | `/runs/{run_id}/explanation` | just the DecisionRecord |

## Editor UI

For creating/editing agents through a browser instead of by hand — see `docs/editor-ui.md`. Quick version:

```powershell
cd frontend && npm install && npm run build && cd ..
python -m uvicorn backend.main:app --reload --port 8080
# then http://127.0.0.1:8080
```

```powershell
curl.exe -X POST http://127.0.0.1:8080/agents/lead_qualification/invoke `
  -H "Content-Type: application/json" `
  -d '{\"lead_id\":\"SME-1001\"}'

curl.exe -X POST http://127.0.0.1:8080/workflows/commercial_leadgen_demo/invoke `
  -H "Content-Type: application/json" `
  -d '{\"industry\":\"manufacturing\",\"location\":\"Chennai\",\"business_need\":\"working_capital\"}'
```

## Tests

```powershell
python -m pytest tests/ -v
```

55 tests (+1 skipped live-only smoke test), all fast (~1.5s) because every test monkeypatches the LLM adapter — no network dependency, no live Ollama needed. See `docs/testing.md`.

## Pulling a source page (build time only)

Every number FinGuru quotes comes from a page someone fetched and read. `webclaw`
is the extractor for that — it keeps HTML tables as tables, where the old
regex tag-strip flattened them into a run of words.

Install once (not vendored — it is ~25 MB and this repo is public):

1. Download `webclaw-vX.Y.Z-x86_64-pc-windows-msvc.zip` from
   [github.com/0xMassi/webclaw/releases](https://github.com/0xMassi/webclaw/releases),
   verify it against the release's `SHA256SUMS`
2. Extract to `%LOCALAPPDATA%\webclaw\` (searched automatically), or put it on
   PATH, or set `WEBCLAW_BIN` to the executable

No API key is needed. `WEBCLAW_API_KEY` only buys hosted JS-rendering and
bot-wall bypass; both rbi.org.in and sbi.bank.in return complete
server-rendered HTML to a plain GET.

```powershell
# confirm the install works against this repo's own two sources
python scripts/webclaw_fetch.py --self-test

# extract one page (markdown, boilerplate stripped)
python scripts/webclaw_fetch.py https://sbi.bank.in/... --out page.md

# with webclaw's parsed metadata (title, language, word_count, JSON-LD)
python scripts/webclaw_fetch.py <url> --json --out page.json
```

**Read the output before anything goes into a fixture.** On genuine `<table>`
markup the extraction is reliable; on SBI's hand-built layout tables the coupon
codes and the categories both survive but their pairing does not. Nothing here
writes a fixture automatically, and that is deliberate — see `## 19` in
`docs/DECISIONS.md`.

## Where results go

- `runs/<run_id>.json` — everything about one run: input summary, per-stage results, final decision, full explanation, error (if any)
- `logs/agent-runs.jsonl` — structured event log, one JSON object per line, every stage start/end and every LLM call with latency and token counts

## Chatting with the underlying model directly (not the agent)

This is unrelated to the agent code — useful for exploring what the model can do, not for testing the agent. Open-WebUI is already installed:

```powershell
open-webui serve
```

Then http://localhost:8080, and under Admin Settings → Connections → Ollama API, point it at the same `OLLAMA_HOST` above. That's a normal multi-turn chat UI, separate from anything in this repo.

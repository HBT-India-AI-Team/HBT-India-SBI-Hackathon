"""FastAPI surface over the Skill-Driven Agent Runtime. Thin by design: it
does input validation and HTTP shaping, then delegates to
agent_platform.runtime.executor.invoke_agent — the same function cli.py
calls. Adding a future agent means adding YAML/Markdown under agents/ and
skills_library/; this file needs no changes, since every route is already
generic over agent_id.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import capabilities_impl  # noqa: E402,F401  (registers mock tools)
from agent_platform.composition import list_agents, load_agent  # noqa: E402
from agent_platform.runtime import chat  # noqa: E402
from agent_platform.runtime.executor import invoke_agent  # noqa: E402
from agent_platform.state import get_run, list_runs  # noqa: E402
from agent_platform.workflows import list_workflows, run_workflow  # noqa: E402

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import admin, api_keys, embed_page  # noqa: E402

app = FastAPI(
    title="Reusable Agent Runtime",
    description="Skill-driven agent runtime demo — Lead Discovery, Lead Qualification and Proposal agents",
    version="1.0.0",
)

# Wide open by design for the demo: a client's own site (on the same LAN,
# or wherever it's hosted) needs to call POST /agents/{id}/invoke directly
# from browser JS, and /admin isn't meant to be reachable outside this
# network anyway. Tighten this before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/agents")
def get_agents() -> dict:
    return {"agents": list_agents()}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    try:
        bundle = load_agent(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {
        "agent_id": bundle.definition.agent_id,
        "version": bundle.definition.version,
        "purpose": bundle.definition.purpose,
        "skills": bundle.definition.skills,
        "pipeline": bundle.definition.pipeline,
        "capabilities": [c.name for c in bundle.definition.capabilities],
        "governance": bundle.definition.governance.model_dump(),
    }


@app.post("/agents/{agent_id}/invoke")
def invoke(agent_id: str, request: dict[str, Any], x_api_key: str | None = Header(default=None)) -> dict:
    """Body is the agent's raw input as-is (e.g. {"lead_id": "SME-1001"} for
    lead_qualification, {"industry": ..., "location": ...} for
    lead_discovery) — generic over any agent's input_schema. An optional
    `correlation_id` key is pulled out rather than passed through.

    Requires the agent's own API key in the X-API-Key header — this is the
    endpoint a client's own site calls once an agent is created and its key
    handed out via the admin UI.
    """
    if not api_keys.is_valid(agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key for this agent")

    request = dict(request)
    correlation_id = request.pop("correlation_id", None)
    try:
        ctx = invoke_agent(agent_id, raw_input=request, correlation_id=correlation_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    return {
        "run_id": ctx.run_id,
        "outcome": (ctx.decision or {}).get("outcome"),
        "decision": ctx.decision,
        # The narrated/generated payload — the only place a text-output (or any
        # non-decision) skill's actual result lives, since `decision` stays null
        # when the pipeline never runs decide (e.g. dialogue/conversational skills).
        "output": ctx.validated_output,
        "hitl": ctx.hitl,
        "error": ctx.error,
    }


@app.post("/agents/{agent_id}/chat")
def chat_with_agent(agent_id: str, request: dict[str, Any], x_api_key: str | None = Header(default=None)) -> dict:
    """The public, key-gated counterpart to /invoke: same auth, but takes
    free-text ({"session_id": str | null, "message": str}) instead of a
    structured evidence dict, and remembers the conversation via session_id
    — what a client's embedded chat (see /embed/{agent_id} below) calls.
    """
    if not api_keys.is_valid(agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key for this agent")

    message = (request.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    try:
        result = chat.handle_chat_turn(agent_id, request.get("session_id"), message)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    return {
        "session_id": result.session_id, "reply": result.reply,
        "evidence": result.evidence, "decision": result.decision, "done": result.done,
        "content_type": result.content_type,
    }


@app.get("/embed/{agent_id}", response_class=HTMLResponse)
def embed_chat_page(agent_id: str) -> str:
    """What a client's site iframes in: a standalone chat page, no build
    step, that talks to POST /agents/{agent_id}/chat same-origin (no CORS
    needed) and keeps its session_id in sessionStorage so a reload during
    the same visit resumes the same conversation.
    """
    try:
        bundle = load_agent(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    api_key = api_keys.get_or_create_key(agent_id)
    return embed_page.render_embed_page(agent_id, api_key, bundle.definition.purpose)


@app.get("/workflows")
def get_workflows() -> dict:
    return {"workflows": list_workflows()}


@app.post("/workflows/{workflow_id}/invoke")
def invoke_workflow(workflow_id: str, request: dict[str, Any]) -> dict:
    request = dict(request)
    correlation_id = request.pop("correlation_id", None)
    try:
        return run_workflow(workflow_id, request, correlation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow_id '{workflow_id}'")


@app.get("/runs")
def get_runs(limit: int = 50) -> dict:
    return {"runs": list_runs(limit=limit)}


@app.get("/runs/{run_id}")
def get_run_detail(run_id: str) -> dict:
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id '{run_id}'")
    return record


@app.get("/runs/{run_id}/explanation")
def get_run_explanation(run_id: str) -> dict:
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id '{run_id}'")
    if record.get("explanation") is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' has no explanation")
    return record["explanation"]


class _HttpOnlyStaticFiles(StaticFiles):
    """StaticFiles asserts scope["type"] == "http" and raises otherwise, which
    crashes the whole ASGI connection (with a scary traceback) whenever a
    browser extension or stray client opens a WebSocket to this catch-all
    mount. Close those cleanly instead of blowing up.
    """

    async def __call__(self, scope, receive, send):  # noqa: D102
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1000})
            return
        await super().__call__(scope, receive, send)


# Serves the built React editor UI (`npm run build` in frontend/) once it
# exists. Must be mounted last — Starlette tries every route above first,
# so this only ever catches paths none of the API routes matched. During
# active frontend development, run the Vite dev server instead (it talks to
# this API via CORS) and this mount is simply unused.
_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", _HttpOnlyStaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

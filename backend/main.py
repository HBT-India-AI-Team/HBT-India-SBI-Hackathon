"""FastAPI surface over the Skill-Driven Agent Runtime. Thin by design: it
does input validation and HTTP shaping, then delegates to
agent_platform.runtime.executor.invoke_agent — the same function cli.py
calls. Adding a future agent means adding YAML/Markdown under agents/ and
skills_library/; this file needs no changes, since every route is already
generic over agent_id.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from contextvars import copy_context
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import capabilities_impl  # noqa: E402,F401  (registers mock tools)
from agent_platform.composition import list_agents, load_agent  # noqa: E402
from agent_platform.llm import speech_stream  # noqa: E402
from agent_platform.runtime import chat, chat_store  # noqa: E402
from agent_platform.runtime.executor import invoke_agent  # noqa: E402
from agent_platform.state import get_run, list_runs  # noqa: E402
from agent_platform.workflows import list_workflows, run_workflow  # noqa: E402

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import admin, api_keys, embed_page, tool_routes, tool_suggest  # noqa: E402

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
app.include_router(tool_routes.router)


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

    question = _request_message(request)
    output = ctx.validated_output or {}
    # Record the exchange when the caller identified itself, so GET /api/history
    # has something to return. No identity means no write, which is exactly
    # today's behaviour for every existing integration.
    session_id = chat_store.record_turn(
        agent_id=agent_id, identity=_request_identity(request),
        question=question, answer=output.get("content") or "",
    )

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
        # Present only when the turn was recorded. A client that ignores it
        # loses nothing; one that keeps it can resume by session instead of
        # replaying its own history.
        "session_id": session_id,
        # Calculators to open beside the reply. Additive — [] on almost every
        # turn, and a client that does not know the key ignores it.
        "tools": tool_suggest.suggest(
            question,
            [{"detail": r.detail} for r in ctx.stage_results],
        ),
    }


# What different callers name the person. The app's identity IS a name --
# "the `name` IS the session key -- no separate random session id is
# generated" (its lib/finguruIdentity.js) -- while our own routes use user_id.
# Both map onto one identity namespace rather than two half-populated ones.
_IDENTITY_KEYS = ("user_id", "name")


def _request_identity(request: dict[str, Any]) -> str:
    """Who this turn belongs to, at either nesting level, under either name."""
    for candidate in (request, request.get("evidence")):
        if not isinstance(candidate, dict):
            continue
        for key in _IDENTITY_KEYS:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


@app.get("/api/history")
def get_history(name: str | None = None, user_id: str | None = None,
                agent_id: str = "finguru") -> dict[str, Any]:
    """The stored transcript for a person, for redrawing a returning user's
    thread. `name` and `user_id` are the same namespace; either works.

    Message shape is {role, text}: the app renders `m.role || m.direction` and
    `m.text || m.content || m.message`, so this hits the first branch of both
    rather than relying on its fallbacks.

    An unknown name is 200 with an empty list, not 404 — "nobody by that name
    has asked anything yet" is a normal state on first visit, and the client
    treats a failed fetch as a soft no-op it cannot distinguish from an error.
    """
    identity = (name or user_id or "").strip()
    if not identity:
        raise HTTPException(status_code=400, detail="Pass ?name= or ?user_id=")

    session = chat_store.get_session_for_user(identity, agent_id)
    messages = (session or {}).get("messages") or []
    return {
        "name": identity,
        "agent_id": agent_id,
        "session_id": (session or {}).get("session_id"),
        "messages": [
            {"role": m.get("role"), "text": m.get("content", "")}
            for m in messages if isinstance(m, dict)
        ],
    }


def _bool_field(value: Any, *, default: bool) -> bool:
    """Read a boolean the way clients actually send them.

    This route takes a raw dict rather than a Pydantic model, so nothing
    coerces on its behalf. `value is not False` reads as sufficient and is
    not: a client sending {"style": "false"} — a stringified bool, which is
    what form encoding and several mobile HTTP libraries produce — would get
    style ON while asking for it OFF. Silently, and disagreeing with the
    admin route, which does have Pydantic and coerces the same payload the
    other way.

    Anything genuinely unreadable falls back to the default rather than
    erroring. A malformed optional flag must not cost someone their answer.
    """
    if isinstance(value, bool):       # before int: bool IS an int in Python
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"false", "0", "no", "off"}:
            return False
        if text in {"true", "1", "yes", "on"}:
            return True
    return default


def _style_summary(stage_trace: list[dict[str, Any]] | None) -> dict | None:
    """Whether the vernacular layer reached this answer, for the client.

    Reported because the layer has several ways to produce nothing — switched
    off, a script with no corpus, nothing above the retrieval floor — and they
    are indistinguishable in the reply itself. A client that cannot see this
    reports "the flag does nothing" for the case where the flag worked
    perfectly and the corpus simply had no match. None on agents that do not
    run the rich-content path.
    """
    for entry in stage_trace or []:
        detail = entry.get("detail")
        if isinstance(detail, dict) and "style" in detail:
            return detail["style"]
    return None


def _request_message(request: dict[str, Any]) -> str:
    """The user's own words, however this caller nested and named them.

    Reuses the pipeline's own resolver rather than reaching for
    request["message"]: /invoke callers say `question`, the chat route says
    `message`, and the voice client nests either under `evidence`. Reading one
    shape has already been the cause of three silent no-ops here.
    """
    from agent_platform.stages.pipeline_stages import _user_message

    return _user_message(request)


def _invoke_stream_events(agent_id: str, request: dict[str, Any]):
    """Sentences as they are finished, then the same payload /invoke returns.

    Same body and same key as /invoke — only the response differs, so a client
    switching over changes a URL and how it reads the reply, nothing else.

    Events, each a `data:` line of JSON:

        {"event": "sentence", "index": 1, "text": "…", "elapsed_ms": 2630.8}
        {"event": "done", "output": {...}, "run_id": "…", "decision": …}
        {"event": "error", "message": "…"}

    The run happens on a worker thread pushing into a queue that this
    generator drains, because the pipeline is synchronous throughout and the
    whole point is to emit before it has finished. The sink is carried into
    that thread by copy_context — a ContextVar does not cross a thread
    boundary on its own, and getting that wrong would look exactly like
    "streaming produced no sentences".
    """
    events: queue.Queue = queue.Queue()
    outcome: dict[str, Any] = {}

    def sink(text: str, _language: str | None) -> None:
        events.put({"event": "sentence", "text": text})

    def worker() -> None:
        speech_stream.sentence_sink.set(sink)
        try:
            ctx = invoke_agent(agent_id, raw_input=dict(request))
            outcome["ctx"] = ctx
        except Exception as exc:                # noqa: BLE001 - relayed below
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            events.put(None)

    # copy_context so the sink set above is visible to the pipeline, which
    # runs inside this thread.
    threading.Thread(target=copy_context().run, args=(worker,), daemon=True).start()

    started = time.perf_counter()
    index = 0
    while True:
        event = events.get()
        if event is None:
            break
        index += 1
        event["index"] = index
        event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    if "error" in outcome:
        yield f"data: {json.dumps({'event': 'error', 'message': outcome['error']})}\n\n"
        return

    ctx = outcome.get("ctx")
    if ctx is None or ctx.error:
        message = (ctx.error or {}).get("message") if ctx else "run produced nothing"
        yield f"data: {json.dumps({'event': 'error', 'message': message})}\n\n"
        return

    # The same fields /invoke returns, so a client can treat this as the
    # authoritative reply and the sentences purely as an early preview.
    yield "data: " + json.dumps({
        "event": "done",
        "run_id": ctx.run_id,
        "tools": tool_suggest.suggest(
            _request_message(request),
            [{"detail": r.detail} for r in ctx.stage_results],
        ),
        "output": ctx.validated_output,
        "decision": ctx.decision,
        "hitl": ctx.hitl,
    }, ensure_ascii=False) + "\n\n"


@app.post("/agents/{agent_id}/invoke/stream")
def invoke_agent_stream(agent_id: str, request: dict[str, Any],
                        x_api_key: str | None = Header(default=None)) -> StreamingResponse:
    """Streaming counterpart to /invoke: same body, same key, sentences first.

    For a client that speaks the reply aloud. Waiting for a 10-25 second
    answer before saying any of it is silence then a monologue; this hands
    over each sentence as it is finished so the first words can be spoken
    while the rest is still being written.

    Sentence boundaries are computed here rather than by the client because
    getting them wrong is not cosmetic: this agent's output is money, and
    "₹1,06,398.02" split on the full stop becomes "₹1,06,398." and "02" — two
    utterances, the first a wrong number spoken to someone who cannot see the
    screen.
    """
    if not api_keys.is_valid(agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key for this agent")
    return StreamingResponse(
        _invoke_stream_events(agent_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/agents/{agent_id}/chat")
def chat_with_agent(agent_id: str, request: dict[str, Any], x_api_key: str | None = Header(default=None)) -> dict:
    """The public, key-gated counterpart to /invoke: same auth, but takes
    free-text ({"session_id": str | null, "message": str}) instead of a
    structured evidence dict, and remembers the conversation via session_id
    — what a client's embedded chat (see /embed/{agent_id} below) calls.

    Two optional flags, both safe to omit:

      "style": false   opt out of the vernacular wording layer (default on,
                       so a client written before the flag keeps its
                       behaviour)
      "voice":  true   the reply will be spoken aloud — two to four
                       sentences, plain prose, no markdown (default off)
    """
    if not api_keys.is_valid(agent_id, x_api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key for this agent")

    message = (request.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    try:
        result = chat.handle_chat_turn(
            agent_id, request.get("session_id"), message,
            _bool_field(request.get("style"), default=True),
            _bool_field(request.get("voice"), default=False),
            user_id=request.get("user_id"),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    return {
        "session_id": result.session_id, "reply": result.reply,
        "evidence": result.evidence, "decision": result.decision, "done": result.done,
        "content_type": result.content_type,
        # Additive: a client that does not know these keys ignores them.
        "style": _style_summary(result.stage_trace),
        # Calculators to open beside the reply — [] for almost every turn.
        "tools": tool_suggest.suggest(message, result.stage_trace),
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

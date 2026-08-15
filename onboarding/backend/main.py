"""
FastAPI app entrypoint for YONO 3.0 backend.

Mounts applications/sessions/admin/users routers, sets up permissive CORS
(hackathon-style prototype -- any origin can call this API so the separate
frontend project can reach it without extra config), and a lifespan event
that:
  - creates DB tables if they don't exist yet (safety net; scripts/init_db.py
    is the documented way to do this explicitly)
  - seeds the recurring idle_nudge_check ScheduledJob (Phase 4) if missing
  - starts the Phase 4 background scheduler poller as an asyncio task
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.logging_config import setup_logging

setup_logging()

from backend.models.db import Base, engine
from backend.models import models  # noqa: F401 -- registers all models on Base
from backend.routers import applications, sessions, admin, users, webhooks, calls, finguru
from backend.services import scheduler

logger = logging.getLogger("yono.main")

_scheduler_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_task
    Base.metadata.create_all(bind=engine)
    scheduler.seed_idle_nudge_job_if_missing()
    _scheduler_task = asyncio.create_task(scheduler.poller_loop())
    logger.info("[main] startup complete -- scheduler poller running")
    yield
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    logger.info("[main] shutdown complete")


app = FastAPI(title="YONO 3.0 Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(applications.router)
app.include_router(sessions.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(webhooks.router)
app.include_router(calls.router)
app.include_router(finguru.router)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Logs every request: method, path, status code, duration_ms. At
    DEBUG level, also logs the request body for POST/PUT (best-effort --
    body is read and re-injected so downstream handlers still see it)."""
    start = time.monotonic()
    body_snippet = None
    if logger.isEnabledFor(logging.DEBUG) and request.method in ("POST", "PUT", "PATCH"):
        raw_body = await request.body()

        async def _receive():
            return {"type": "http.request", "body": raw_body, "more_body": False}

        request._receive = _receive
        try:
            body_snippet = raw_body[:2000].decode("utf-8", errors="replace")
        except Exception:
            body_snippet = "<unable to decode body>"

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "[http] %s %s -> UNHANDLED EXCEPTION (%.1fms)",
            request.method, request.url.path, duration_ms,
        )
        raise

    duration_ms = (time.monotonic() - start) * 1000
    log_level = logging.INFO if response.status_code < 400 else logging.WARNING
    logger.log(
        log_level, "[http] %s %s -> %s (%.1fms)",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    if body_snippet is not None:
        logger.debug("[http] %s %s request body: %s", request.method, request.url.path, body_snippet)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Ensures unhandled errors are always logged with a full traceback
    (not just silently surfaced as a bare 500 to the client) before
    returning a generic 500 response."""
    logger.exception("[main] unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})


@app.get("/health")
def health():
    return {"ok": True, "service": "yono-3.0-backend"}

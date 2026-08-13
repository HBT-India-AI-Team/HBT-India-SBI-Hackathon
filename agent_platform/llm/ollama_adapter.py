"""LLMProvider backed by a local Ollama server.

Uses Ollama's structured-output support (`format: <json schema>`) so the
model is constrained to the skill's output contract rather than trusted to
free-form its way into valid JSON. Talks to the server over plain HTTP via
`requests` (already a project dependency) — no new client library needed.

Retries transient failures (connection errors, 502/503/504) a bounded
number of times with a fixed backoff before giving up — observed in
practice against the shared demo tunnel, which occasionally 503s a second
request arriving right after a first one completes. Non-transient errors
(bad request, model not found, ...) fail immediately; the caller's own
graceful-degradation path (reason_llm stage -> deterministic fallback) is
what handles a final, exhausted-retries failure.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

_TRANSIENT_STATUS_CODES = {502, 503, 504}

# Every attempt of every call this adapter makes (including failed retries),
# across every caller in the platform — pipeline runs, agent generation,
# refine, chat — since this is the one chokepoint they all go through. One
# line per attempt, not just the final outcome, since diagnosing flaky
# behavior (this platform's real Ollama host has been unreliable) needs to
# see individual retries, not just "it eventually failed." Not wired
# through agent_platform.observability.logging.AgentLogger since most
# callers here (agent_builder.py in particular) have no RunContext at all.
_CALLS_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "ollama_calls.jsonl"


class OllamaError(RuntimeError):
    pass


class OllamaContentError(OllamaError):
    """Raised when the HTTP call itself succeeded but the model's message
    content wasn't usable JSON — distinct from a transport/status failure
    (already retried inside _post_chat) so callers can choose to retry this
    case separately. Seen in practice with "thinking"-capable models that
    spend their whole output budget on reasoning and return empty content.
    """


# Everything a Logs row renders before you expand it. The request/response
# bodies are deliberately not here: a single record can carry a 130KB prompt
# plus its completion, so sending whole records for a 100-row list meant a
# multi-megabyte response for a page that shows six short fields per row.
# Expanding a row fetches that one record's body via read_call() instead.
_SUMMARY_FIELDS = ("timestamp", "host", "model", "attempt", "total_attempts",
                   "duration_ms", "ok", "error")


def _tail_records(limit: int) -> list[tuple[int, dict[str, Any]]]:
    """The last `limit` records as (byte_offset, parsed) pairs, oldest first.

    Reads backwards in chunks rather than pulling the whole file in, so cost
    tracks `limit` instead of however large the log has grown — this file is
    append-only and never rotated, and its sibling agent-runs.jsonl is
    already tens of megabytes.

    The byte offset doubles as each record's stable ID: appends never move
    earlier lines, so read_call() can seek straight to one.
    """
    chunk_size = 64 * 1024
    with _CALLS_LOG_PATH.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buffer = b""
        # One newline more than `limit` so the (possibly partial) leading
        # line can be dropped below and still leave `limit` whole ones.
        while pos > 0 and buffer.count(b"\n") <= limit:
            step = min(chunk_size, pos)
            pos -= step
            f.seek(pos)
            buffer = f.read(step) + buffer

    start = pos
    if start > 0:
        # Landed mid-record; the next newline is the first whole one.
        partial, _, rest = buffer.partition(b"\n")
        start += len(partial) + 1
        buffer = rest

    records: list[tuple[int, dict[str, Any]]] = []
    offset = start
    for raw in buffer.split(b"\n"):
        if raw.strip():
            try:
                records.append((offset, json.loads(raw)))
            except json.JSONDecodeError:
                pass
        offset += len(raw) + 1
    return records[-limit:]


def read_recent_calls(limit: int = 100) -> list[dict[str, Any]]:
    """Most-recent-first summaries — every field the Logs list renders, plus
    the `offset` to pass back to read_call() for the bodies. Kept here rather
    than having admin.py parse the JSONL file itself, so the log format stays
    encapsulated with the code that writes it.
    """
    if not _CALLS_LOG_PATH.exists():
        return []
    summaries = [
        {**{field: record.get(field) for field in _SUMMARY_FIELDS}, "offset": offset}
        for offset, record in _tail_records(limit)
    ]
    summaries.reverse()
    return summaries


def read_call(offset: int) -> dict[str, Any] | None:
    """One full record — bodies included — by the `offset` read_recent_calls
    handed out. None if the offset doesn't start a valid record, which is
    what a stale offset from a deleted/truncated log looks like.
    """
    if offset < 0 or not _CALLS_LOG_PATH.exists():
        return None
    with _CALLS_LOG_PATH.open("rb") as f:
        f.seek(offset)
        raw = f.readline()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class OllamaAdapter:
    def __init__(self, host: str = "http://localhost:11434", model: str = "gemma4:12b",
                 timeout_seconds: int = 120, seed: int = 7,
                 max_transient_retries: int = 3, retry_backoff_seconds: float = 5.0,
                 think: bool | None = None):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.seed = seed
        self.max_transient_retries = max_transient_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        # Ollama's per-request thinking switch. Left as None the parameter is
        # not sent at all, so every existing agent keeps whatever the model
        # does by default -- this must stay opt-in, because the setting is not
        # portable: gemma4:12b handles think=false fine, gpt-oss:20b returns
        # empty content when given it. Set it per agent, having tested that
        # agent's model, never globally.
        self.think = think

    def _log_call(self, *, attempt: int, total_attempts: int, payload: dict[str, Any],
                  duration_ms: float, ok: bool, response_body: dict[str, Any] | None = None,
                  error: str | None = None) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "host": self.host,
            "model": self.model,
            "attempt": attempt,
            "total_attempts": total_attempts,
            "duration_ms": round(duration_ms, 2),
            "ok": ok,
            "request": {
                "messages": payload.get("messages"),
                "format": payload.get("format"),
                "tools": payload.get("tools"),
                "options": payload.get("options"),
            },
            "response": response_body,
            "error": error,
        }
        try:
            _CALLS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _CALLS_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass  # logging must never be why a real call fails

    def _post_chat(self, payload: dict[str, Any], *, think: bool | None = None) -> dict[str, Any]:
        """`think` is per-call, NOT taken from self, because the two calls this
        adapter makes need opposite settings on a reasoning model.

        Measured on qwen3.6:35b behind FinGuru's 21k-character instructions:

          tool loop, thinking ON   -> tools called at every prompt length tested
          tool loop, thinking OFF  -> no tool calls at 12k and 21k
          final answer, thinking ON  -> 3,457 chars of reasoning, 81 of answer
          final answer, thinking OFF -> a full answer

        Deciding *which* tool to call is reasoning, so suppressing it costs
        exactly the capability the agent is built on. Writing the answer from
        results already gathered is not, and there the reasoning budget gets
        spent instead of the reply. Setting this globally fixes one and breaks
        the other -- which is how it was found: silencing thinking to stop
        empty replies silently stopped every tool call.
        """
        payload = {**payload, "model": self.model, "stream": False}
        if think is not None:
            payload["think"] = think
        total_attempts = self.max_transient_retries + 1
        for attempt in range(1, total_attempts + 1):
            t0 = time.perf_counter()
            try:
                response = requests.post(
                    f"{self.host}/api/chat", json=payload, timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                self._log_call(
                    attempt=attempt, total_attempts=total_attempts, payload=payload,
                    duration_ms=(time.perf_counter() - t0) * 1000, ok=True, response_body=body,
                )
                return body
            except requests.RequestException as exc:
                status = exc.response.status_code if getattr(exc, "response", None) is not None else None
                transient = isinstance(exc, requests.ConnectionError) or status in _TRANSIENT_STATUS_CODES
                self._log_call(
                    attempt=attempt, total_attempts=total_attempts, payload=payload,
                    duration_ms=(time.perf_counter() - t0) * 1000, ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if not transient or attempt == total_attempts:
                    raise OllamaError(f"Ollama request failed after {attempt} attempt(s): {exc}") from exc
                time.sleep(self.retry_backoff_seconds)
        raise AssertionError("unreachable")  # loop always returns or raises

    def _call_metadata(self, body: dict[str, Any], duration_ms: float) -> dict[str, Any]:
        """What a caller needs to explain the call afterwards, pulled from
        Ollama's own response rather than reconstructed.

        `thinking` is the model's actual reasoning trace. Reasoning-capable
        models (gemma4 among them) return it as a sibling of `content` in the
        message, and it was previously discarded here — every caller only
        read `content`. Surfacing it is what lets the Playground show real
        chain-of-thought instead of a narrated guess at what the model did.
        Models that don't reason return no such field, in which case this is
        None and the UI shows nothing rather than inventing a story.
        """
        message = body.get("message") or {}
        thinking = message.get("thinking")
        return {
            "model": body.get("model") or self.model,
            "duration_ms": round(duration_ms, 2),
            "prompt_tokens": body.get("prompt_eval_count"),
            "completion_tokens": body.get("eval_count"),
            "done_reason": body.get("done_reason"),
            "thinking": thinking.strip() if isinstance(thinking, str) and thinking.strip() else None,
        }

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        t0 = time.perf_counter()
        body = self._post_chat({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": schema,
            "options": {"temperature": temperature, "seed": self.seed},
        }, think=self.think)   # writing the answer: suppress reasoning if configured
        duration_ms = (time.perf_counter() - t0) * 1000
        message = body.get("message", {})
        content = message.get("content", "")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaContentError(f"Ollama returned non-JSON content: {content[:200]!r}") from exc

        return parsed, self._call_metadata(body, duration_ms)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> tuple[str, dict[str, Any]]:
        """Same chokepoint as generate_structured, but with no `format`
        constraint — for agents whose whole point is emitting plain text
        (dialogue lines, prose), not a JSON object.

        Raises OllamaContentError on empty content, same as
        generate_structured's non-JSON case — seen in practice with
        "thinking"-capable models (gemma4:12b included) that sometimes burn
        their entire output budget on reasoning (`done_reason: "length"`,
        tens of thousands of characters of `message.thinking`) and never
        emit the actual answer. Not a transport failure, so the caller
        decides whether to retry, exactly like the generate_structured case.
        """
        t0 = time.perf_counter()
        body = self._post_chat({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": temperature, "seed": self.seed},
        }, think=self.think)   # also prose, same reasoning as generate_structured
        duration_ms = (time.perf_counter() - t0) * 1000
        content = body.get("message", {}).get("content", "")

        if not content.strip():
            raise OllamaContentError(
                f"Ollama returned empty content (done_reason={body.get('done_reason')!r}) — "
                f"the model likely exhausted its output budget on internal reasoning."
            )

        return content, self._call_metadata(body, duration_ms)

    def run_tool_loop(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        resolve_tool: Callable[[str, dict[str, Any]], str],
        max_turns: int = 4,
        temperature: float = 0.0,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        calls_made: list[dict[str, Any]] = []

        for _turn in range(max_turns):
            body = self._post_chat({
                "messages": messages,
                "tools": tools,
                "options": {"temperature": temperature, "seed": self.seed},
            })   # think deliberately NOT passed: choosing tools IS the reasoning
            message = body.get("message", {})
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                break

            messages.append({"role": "assistant", "content": message.get("content", ""),
                              "tool_calls": tool_calls})
            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                calls_made.append({"name": name, "arguments": arguments})
                result = resolve_tool(name, arguments)
                messages.append({"role": "tool", "content": result})

        return calls_made

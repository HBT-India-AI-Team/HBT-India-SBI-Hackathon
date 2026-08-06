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
from typing import Any, Callable

import requests

_TRANSIENT_STATUS_CODES = {502, 503, 504}


class OllamaError(RuntimeError):
    pass


class OllamaAdapter:
    def __init__(self, host: str = "http://localhost:11434", model: str = "gemma4:12b",
                 timeout_seconds: int = 120, seed: int = 7,
                 max_transient_retries: int = 3, retry_backoff_seconds: float = 5.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.seed = seed
        self.max_transient_retries = max_transient_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "model": self.model, "stream": False}
        total_attempts = self.max_transient_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                response = requests.post(
                    f"{self.host}/api/chat", json=payload, timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                status = exc.response.status_code if getattr(exc, "response", None) is not None else None
                transient = isinstance(exc, requests.ConnectionError) or status in _TRANSIENT_STATUS_CODES
                if not transient or attempt == total_attempts:
                    raise OllamaError(f"Ollama request failed after {attempt} attempt(s): {exc}") from exc
                time.sleep(self.retry_backoff_seconds)
        raise AssertionError("unreachable")  # loop always returns or raises

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
        })
        duration_ms = (time.perf_counter() - t0) * 1000
        content = body.get("message", {}).get("content", "")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned non-JSON content: {content[:200]!r}") from exc

        metadata = {
            "model": self.model,
            "duration_ms": round(duration_ms, 2),
            "prompt_tokens": body.get("prompt_eval_count"),
            "completion_tokens": body.get("eval_count"),
        }
        return parsed, metadata

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
            })
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

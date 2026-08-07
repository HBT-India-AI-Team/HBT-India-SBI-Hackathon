"""Tests for OllamaAdapter's per-attempt call logging (agent_platform/llm/
ollama_adapter.py's _log_call) — mocks the HTTP layer the same way
test_tool_loop.py does, and points the log file at a tmp path so tests never
touch the real logs/ollama_calls.jsonl.
"""
import json

import pytest
import requests

from agent_platform.llm import ollama_adapter as ollama_adapter_module
from agent_platform.llm.ollama_adapter import OllamaAdapter


class FakeResponse:
    def __init__(self, body: dict, status_code: int | None = None):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code is not None and self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self) -> dict:
        return self._body


def _adapter(tmp_path, monkeypatch, **kwargs) -> OllamaAdapter:
    monkeypatch.setattr(ollama_adapter_module, "_CALLS_LOG_PATH", tmp_path / "ollama_calls.jsonl")
    return OllamaAdapter(host="http://fake-host", model="fake-model", retry_backoff_seconds=0, **kwargs)


def _read_log(tmp_path) -> list[dict]:
    path = tmp_path / "ollama_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_successful_call_logs_one_ok_entry_with_request_and_response(tmp_path, monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({"message": {"role": "assistant", "content": "hi"}})

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)
    adapter = _adapter(tmp_path, monkeypatch)

    adapter._post_chat({"messages": [{"role": "user", "content": "hello"}]})

    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["ok"] is True
    assert entries[0]["attempt"] == 1
    assert entries[0]["error"] is None
    assert entries[0]["request"]["messages"] == [{"role": "user", "content": "hello"}]
    assert entries[0]["response"]["message"]["content"] == "hi"


def test_transient_failure_then_success_logs_both_attempts(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse({}, status_code=503)
        return FakeResponse({"message": {"role": "assistant", "content": "ok now"}})

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)
    adapter = _adapter(tmp_path, monkeypatch)

    adapter._post_chat({"messages": []})

    entries = _read_log(tmp_path)
    assert len(entries) == 2
    assert entries[0]["ok"] is False
    assert entries[0]["attempt"] == 1
    assert "503" in entries[0]["error"]
    assert entries[1]["ok"] is True
    assert entries[1]["attempt"] == 2


def test_exhausted_retries_logs_every_attempt_and_raises(tmp_path, monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({}, status_code=503)

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)
    adapter = _adapter(tmp_path, monkeypatch, max_transient_retries=2)

    with pytest.raises(ollama_adapter_module.OllamaError):
        adapter._post_chat({"messages": []})

    entries = _read_log(tmp_path)
    assert len(entries) == 3  # 1 initial + 2 retries
    assert all(e["ok"] is False for e in entries)
    assert [e["attempt"] for e in entries] == [1, 2, 3]

"""Tests for OllamaAdapter.run_tool_loop's bounded looping logic — mocks the
HTTP layer (agent_platform.llm.ollama_adapter.requests.post) rather than
hitting a real Ollama server, mirroring how generate_structured itself isn't
tested against a live server anywhere in this suite. The wire protocol this
mocks was validated against a real local model in a one-off spike before
building this (Ollama /api/chat with `tools`, tool_calls in the response
message) — this file protects the looping/turn-bounding logic around it.
"""
from agent_platform.llm.ollama_adapter import OllamaAdapter

_TOOLS = [{"type": "function", "function": {"name": "load_skill", "parameters": {}}}]


class FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._body


def _tool_call_body(*skill_ids: str) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "load_skill", "arguments": {"skill_id": sid}}}
                for sid in skill_ids
            ],
        }
    }


def _final_body(content: str = "done") -> dict:
    return {"message": {"role": "assistant", "content": content}}


def _adapter() -> OllamaAdapter:
    return OllamaAdapter(host="http://fake-host", model="fake-model")


def test_no_tool_calls_returns_empty_list_after_one_request(monkeypatch):
    posts = []

    def fake_post(url, json, timeout):
        posts.append(json)
        return FakeResponse(_final_body())

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)

    result = _adapter().run_tool_loop(
        system_prompt="sys", user_prompt="user", tools=_TOOLS, resolve_tool=lambda n, a: "unused",
    )

    assert result == []
    assert len(posts) == 1


def test_single_tool_call_then_stop(monkeypatch):
    responses = [_tool_call_body("advisor_risk"), _final_body()]

    def fake_post(url, json, timeout):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)

    resolved = []
    result = _adapter().run_tool_loop(
        system_prompt="sys", user_prompt="user", tools=_TOOLS,
        resolve_tool=lambda name, args: resolved.append((name, args)) or "resolved-text",
    )

    assert result == [{"name": "load_skill", "arguments": {"skill_id": "advisor_risk"}}]
    assert resolved == [("load_skill", {"skill_id": "advisor_risk"})]
    assert responses == []  # both canned responses were consumed


def test_multiple_tool_calls_in_same_turn_all_captured(monkeypatch):
    responses = [_tool_call_body("advisor_risk", "advisor_growth"), _final_body()]

    def fake_post(url, json, timeout):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)

    result = _adapter().run_tool_loop(
        system_prompt="sys", user_prompt="user", tools=_TOOLS, resolve_tool=lambda n, a: "text",
    )

    assert [c["arguments"]["skill_id"] for c in result] == ["advisor_risk", "advisor_growth"]


def test_max_turns_bounds_an_endlessly_tool_calling_model(monkeypatch):
    call_count = {"n": 0}

    def fake_post(url, json, timeout):
        call_count["n"] += 1
        return FakeResponse(_tool_call_body("advisor_risk"))  # never stops on its own

    monkeypatch.setattr("agent_platform.llm.ollama_adapter.requests.post", fake_post)

    result = _adapter().run_tool_loop(
        system_prompt="sys", user_prompt="user", tools=_TOOLS,
        resolve_tool=lambda n, a: "text", max_turns=3,
    )

    assert call_count["n"] == 3
    assert len(result) == 3

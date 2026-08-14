"""Tests for agent_platform/runtime/chat.py — turning free-text conversation
into the same structured evidence invoke_agent() already runs on. Uses the
real lead_qualification agent (dotted-path gate fields like "kyc.status",
"bureau.score" — a good regression guard, since a flat dict.update() merge
would silently corrupt those) with a fake chat-extraction adapter, plus the
existing tests/fakes.py FakeAdapter for reason_llm's narration call.
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.composition.models import AgentDefinition, AgentBundle, Skill
from agent_platform.runtime import chat, chat_store
from agent_platform.stages import pipeline_stages

from fakes import FakeAdapter

_GATE_FIELDS_QUALIFYING = {
    "kyc.status": "VERIFIED",
    "kyc.sanctions_hit": False,
    "lead.business_vintage_years": 5,
    "bureau.default_flag": False,
    "bureau.score": 700,
}


class FakeChatAdapter:
    """Returns one canned (extracted_fields, reply) pair per call, in order —
    same queue-of-responses pattern as test_agent_builder.py's FakeSpecAdapter.
    """

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0

    def generate_structured(self, *, system_prompt, user_prompt, schema, temperature=0.0):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        return turn, {"model": "fake", "duration_ms": 1.0, "prompt_tokens": 1, "completion_tokens": 1}


@pytest.fixture(autouse=True)
def fake_reason_llm(monkeypatch):
    # lead_qualification's own reason_llm narration call, unrelated to chat
    # extraction — always faked so a real decision can be reached without a
    # live Ollama server.
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


@pytest.fixture(autouse=True)
def isolate_chat_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "CHAT_SESSIONS_DIR", tmp_path / "chat_sessions")


def _fake_chat(monkeypatch, *turns):
    adapter = FakeChatAdapter(turns)
    monkeypatch.setattr(chat, "_build_adapter", lambda: adapter)
    return adapter


def test_missing_required_field_asks_follow_up(monkeypatch):
    _fake_chat(monkeypatch, {"extracted_fields": {}, "reply": "What's the KYC status?"})

    result = chat.handle_chat_turn("lead_qualification", None, "I'd like to check eligibility")

    assert result.done is False
    assert result.decision is None
    assert result.reply == "What's the KYC status?"
    assert result.session_id


def test_all_gate_fields_present_reaches_a_decision(monkeypatch):
    _fake_chat(monkeypatch, {"extracted_fields": _GATE_FIELDS_QUALIFYING, "reply": "unused"})

    result = chat.handle_chat_turn("lead_qualification", None, "kyc verified, no sanctions, 5 years, score 700")

    assert result.done is True
    assert result.decision is not None
    assert result.decision["outcome"] in {"QUALIFIED", "CONDITIONALLY_QUALIFIED", "NEEDS_HUMAN_REVIEW", "NOT_QUALIFIED"}
    # dotted fields must have landed nested, not as literal "kyc.status" keys
    assert result.evidence["kyc"]["status"] == "VERIFIED"
    assert result.evidence["bureau"]["score"] == 700


def test_session_persists_evidence_across_two_turns(monkeypatch):
    first_batch = {"kyc.status": "VERIFIED", "kyc.sanctions_hit": False, "lead.business_vintage_years": 5}
    second_batch = {"bureau.default_flag": False, "bureau.score": 700}
    adapter = _fake_chat(
        monkeypatch,
        {"extracted_fields": first_batch, "reply": "What's the bureau score?"},
        {"extracted_fields": second_batch, "reply": "unused"},
    )

    first = chat.handle_chat_turn("lead_qualification", None, "kyc verified, no sanctions, 5 years old")
    assert first.done is False

    second = chat.handle_chat_turn("lead_qualification", first.session_id, "score is 700, no default")
    assert adapter.calls == 2
    assert second.done is True
    assert second.decision is not None
    # evidence from turn 1 must still be present, not overwritten
    assert second.evidence["kyc"]["status"] == "VERIFIED"
    assert second.evidence["bureau"]["score"] == 700


def test_user_id_reuses_same_session_for_same_user(monkeypatch):
    first_batch = {"kyc.status": "VERIFIED", "kyc.sanctions_hit": False, "lead.business_vintage_years": 5}
    second_batch = {"bureau.default_flag": False, "bureau.score": 700}
    adapter = _fake_chat(
        monkeypatch,
        {"extracted_fields": first_batch, "reply": "What's the bureau score?"},
        {"extracted_fields": second_batch, "reply": "unused"},
    )

    first = chat.handle_chat_turn("lead_qualification", None, "kyc verified, no sanctions, 5 years old", user_id="user-42")
    second = chat.handle_chat_turn("lead_qualification", None, "score is 700, no default", user_id="user-42")

    assert first.session_id == second.session_id
    assert adapter.calls == 2
    assert second.done is True
    assert second.evidence["bureau"]["score"] == 700


def test_dotted_field_merge_writes_nested_not_flat():
    evidence = {"kyc": {"status": "PENDING"}}
    changed = chat._merge_evidence(evidence, {"kyc.status": "VERIFIED", "bureau.score": 700})

    assert changed is True
    assert evidence == {"kyc": {"status": "VERIFIED"}, "bureau": {"score": 700}}
    assert "kyc.status" not in evidence  # must not have written a literal dotted key


def test_dotted_field_merge_reports_no_change_when_value_is_the_same():
    evidence = {"kyc": {"status": "VERIFIED"}}
    changed = chat._merge_evidence(evidence, {"kyc.status": "VERIFIED"})
    assert changed is False


def test_guidance_only_agent_uses_plain_conversational_reply(monkeypatch):
    guidance_skill = Skill(
        skill_id="explain_only", version="1.0.0", description="Explains things.",
        instructions_text="Be friendly and explain banking terms simply.",
    )
    definition = AgentDefinition(
        agent_id="guidance_demo", version="1.0.0", purpose="Explain banking terms.",
        skills=["explain_only"], pipeline=["load_input"],
    )
    bundle = AgentBundle(definition=definition, skills={"explain_only": guidance_skill})
    monkeypatch.setattr(chat, "load_agent", lambda agent_id: bundle)
    adapter = FakeChatAdapter([{"reply": "A credit score reflects how reliably you repay debt."}])
    monkeypatch.setattr(chat, "_build_adapter", lambda: adapter)

    result = chat.handle_chat_turn("guidance_demo", None, "what's a credit score?")

    assert result.reply == "A credit score reflects how reliably you repay debt."
    assert result.decision is None
    assert result.done is False

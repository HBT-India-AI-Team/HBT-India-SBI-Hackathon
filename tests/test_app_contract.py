"""The contract the FinGuru app actually sends and reads.

Written against its source rather than against a description of it:
src/api/finguru.js builds the request, pages/FinGuruChat.jsx reads the
response, and finguru-dynamic-tools-frontend-spec.md defines the tool shape.
Each assertion here names the thing on their side that depends on it, so a
change that would break the app fails here first.
"""
from __future__ import annotations

import pytest

from agent_platform.runtime import chat_store
from agent_platform.stages import pipeline_stages


class _Skill:
    instructions_text = "instructions"
    shared_text = ""
    task_prompt_text = ""


# The literal payload src/api/finguru.js:44-51 builds.
def _app_payload(question="FD rate epdi irukku?", history=None):
    return {"evidence": {
        "question": question,
        "history": history if history is not None else [],
        "style": True,
        "voice": False,
        "language": "ta",
        "name": "Dhanush",
    }}


def test_nothing_the_app_sends_leaks_into_the_prompt_as_content():
    """_build_text_prompt renders every unrecognised key into the user prompt.
    `history` and `name` were both unrecognised, so the model received a
    literal Python dict repr pasted into the question."""
    _, user_prompt = pipeline_stages._build_text_prompt(_Skill(), _app_payload())

    assert user_prompt == "evidence: {'question': 'FD rate epdi irukku?'}"
    for routing_key in ("history", "name", "style", "voice", "language"):
        assert routing_key not in user_prompt


def test_history_becomes_a_transcript_not_a_dict_dump():
    """The app sends [{role, content}] (FinGuruChat.jsx:604). It should reach
    the model as something a model reads, and it must not be mistaken for a
    source of figures."""
    history = [
        {"role": "user", "content": "EMI on 20 lakh at 8.5% over 20 years?"},
        {"role": "assistant", "content": "Your monthly EMI would be ₹17,356.46."},
    ]
    _, user_prompt = pipeline_stages._build_text_prompt(
        _Skill(), _app_payload("And what about 15 years?", history))

    assert "user: EMI on 20 lakh at 8.5% over 20 years?" in user_prompt
    assert "assistant: Your monthly EMI would be ₹17,356.46." in user_prompt
    assert "'role':" not in user_prompt          # no repr anywhere
    assert "every figure still comes from a tool call made now" in user_prompt


def test_history_is_read_under_either_name_and_either_level():
    """`history` is the app's name for it; `conversation_history` is what our
    own chat route writes. Same shape, one renderer."""
    turns = [{"role": "user", "content": "hi"}]
    for payload in (
        {"evidence": {"history": turns}},
        {"evidence": {"conversation_history": turns}},
        {"history": turns},
        {"conversation_history": turns},
    ):
        assert pipeline_stages._conversation_history(payload) == turns, payload


def test_the_apps_local_message_shape_is_tolerated():
    """Its IndexedDB store uses {direction: inbound|outbound, text} rather
    than {role, content}. Forwarding that store directly is an easy mistake,
    and silently dropping the history for it would be hard to notice."""
    assert pipeline_stages._conversation_history({"evidence": {"history": [
        {"direction": "inbound", "text": "hi"},
        {"direction": "outbound", "text": "hello"},
    ]}}) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_history_is_bounded():
    """One pasted wall of text must not crowd out the instructions, and an
    unbounded transcript grows every turn forever."""
    long_turns = [{"role": "user", "content": "x" * 2000} for _ in range(30)]
    turns = pipeline_stages._conversation_history({"evidence": {"history": long_turns}})

    assert len(turns) == pipeline_stages._HISTORY_TURNS
    assert all(len(t["content"]) <= pipeline_stages._HISTORY_CHARS + 6 for t in turns)


def test_junk_history_is_skipped_not_fatal():
    assert pipeline_stages._conversation_history({"evidence": {"history": [
        None, "a string", {}, {"role": "user", "content": "   "},
        {"role": "user", "content": "kept"},
    ]}}) == [{"role": "user", "content": "kept"}]
    assert pipeline_stages._conversation_history({"evidence": {"history": "nope"}}) == []


class TestIdentity:
    """The app's identity IS a name -- lib/finguruIdentity.js: "the `name` IS
    the session key". Ours was user_id. One namespace, two spellings."""

    def test_either_spelling_at_either_level(self):
        from backend.main import _request_identity

        assert _request_identity(_app_payload()) == "Dhanush"
        assert _request_identity({"user_id": "u-1"}) == "u-1"
        assert _request_identity({"evidence": {"user_id": "u-1"}}) == "u-1"
        assert _request_identity({"evidence": {"question": "hi"}}) == ""

    def test_user_id_wins_when_both_are_sent(self):
        assert __import__("backend.main", fromlist=["x"])._request_identity(
            {"user_id": "u-1", "name": "Dhanush"}) == "u-1"


class TestHistoryStore:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(chat_store, "CHAT_SESSIONS_DIR", tmp_path)

    def test_a_turn_is_recorded_and_comes_back_in_render_shape(self):
        """The app reads `m.role || m.direction` and `m.text || m.content`
        (FinGuruChat.jsx:987-988), so {role, text} hits the first branch of
        both rather than relying on the fallbacks."""
        from backend.main import get_history

        chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                               question="FD rate?", answer="6.25% for a year.")
        body = get_history(name="Dhanush")

        assert body["messages"] == [
            {"role": "user", "text": "FD rate?"},
            {"role": "assistant", "text": "6.25% for a year."},
        ]
        assert body["session_id"]

    def test_turns_accumulate_into_one_conversation(self):
        for i in range(3):
            chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                                   question=f"q{i}", answer=f"a{i}")
        sessions = {chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                                           question="q", answer="a")}
        assert len(sessions) == 1
        assert len(chat_store.get_session_for_user("Dhanush", "finguru")["messages"]) == 8

    def test_an_unknown_name_is_empty_not_an_error(self):
        """First visit is a normal state, and the client cannot tell a 404
        from a network failure -- both are a soft no-op to it."""
        from backend.main import get_history

        assert get_history(name="Nobody")["messages"] == []

    def test_two_people_do_not_see_each_other(self):
        chat_store.record_turn(agent_id="finguru", identity="Asha",
                               question="mine", answer="hers")
        chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                               question="mine", answer="his")

        from backend.main import get_history
        assert [m["text"] for m in get_history(name="Asha")["messages"]] == ["mine", "hers"]

    def test_recording_never_costs_the_answer(self, monkeypatch):
        """The reply is already generated by this point. A transcript that
        cannot be written is a lesser failure than an error page."""
        monkeypatch.setattr(chat_store, "save_session",
                            lambda _s: (_ for _ in ()).throw(OSError("disk full")))
        assert chat_store.record_turn(agent_id="finguru", identity="x",
                                      question="q", answer="a") is None

    def test_no_identity_writes_nothing(self):
        """Every existing integration sends neither, and must keep getting the
        stateless behaviour it has today."""
        assert chat_store.record_turn(agent_id="finguru", identity="",
                                      question="q", answer="a") is None

    def test_a_corrupt_session_file_is_not_permanent(self):
        """write_text truncates before writing, so a crash mid-write leaves
        half a file. Unguarded, that raised on every later read for that
        person -- forever, until someone deleted it by hand."""
        chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                               question="q", answer="a")
        session_id = chat_store._read_user_index()["Dhanush::finguru"]
        chat_store._session_path(session_id).write_text("{ truncated", encoding="utf-8")

        assert chat_store.get_session(session_id) is None
        # And the next turn starts a fresh conversation rather than 500ing.
        assert chat_store.record_turn(agent_id="finguru", identity="Dhanush",
                                      question="q2", answer="a2")


class TestToolsSpec:
    """finguru-dynamic-tools-frontend-spec.md, §2 and §5."""

    @pytest.fixture(autouse=True)
    def _isolated_db(self, monkeypatch, tmp_path):
        from backend import tool_store
        monkeypatch.setattr(tool_store, "DB_PATH", tmp_path / "tools.db")
        tool_store.init_db()

    def test_every_tool_declares_how_it_executes(self):
        """§3: the renderer branches on `execution`. Omitting it is not
        neutral -- undefined matches neither branch, so the calculator draws
        and then does nothing when submitted."""
        from backend import tool_store

        for tool in tool_store.get_tools():
            assert tool["execution"] == "server"
            # §2: `formula` is omitted for server tools. Nothing ships a copy
            # of the arithmetic to the browser.
            assert "formula" not in tool

        # §6: the saved-tools tab re-renders through the same generic
        # renderer, so the nested definition needs it too.
        tool_store.save_user_tool(user_id="Dhanush", tool_id="emi_calculator",
                                  input_values={"principal": 1, "rate": 1, "months": 1})
        [saved] = tool_store.get_saved_tools(user_id="Dhanush")
        assert saved["tool"]["execution"] == "server"

    def test_execute_returns_the_number_under_result(self):
        """§5: { "result": ..., "output_label": "..." }. `result` used to be
        the whole capability dict, which that client renders as
        "[object Object]"."""
        from backend import tool_store

        body = tool_store.run_tool("emi_calculator",
                                   {"principal": 2000000, "rate": 8.5, "months": 240})

        assert isinstance(body["result"], (int, float))
        assert body["result"] == pytest.approx(17356.46, abs=0.01)
        assert body["output_label"] == "Monthly EMI"
        # Additive, for our own Playground and for anything wanting detail.
        assert body["value"] == body["result"]
        assert body["breakdown"]["total_interest"] > 0


def test_saving_accepts_the_apps_name_key():
    """Its api/finguruHistory.js posts {name, ...} to /api/tools/save. That
    was written, left unwired, and would have 422'd the day it was turned on."""
    from backend.tool_routes import SavePayload

    assert SavePayload(tool_id="emi_calculator", name="Dhanush").identity == "Dhanush"
    assert SavePayload(tool_id="emi_calculator", user_id="u-1").identity == "u-1"
    assert SavePayload(tool_id="emi_calculator", user_id="u-1", name="D").identity == "u-1"
    assert SavePayload(tool_id="emi_calculator").identity == ""

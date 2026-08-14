"""Sentence-at-a-time streaming to speech.

The two pure pieces are tested hard because they are where a bug becomes bad
audio rather than an exception: a rupee figure cut in half, or JSON syntax
read aloud. Both are silent failures — the answer is still correct on screen.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_platform.llm.speech_stream import StreamResult, stream_to_speech
from agent_platform.llm.streaming import JsonStringField, SentenceSplitter


def split_all(text: str, chunk: int = 1) -> list[str]:
    """Feed `text` in fixed-size pieces, the way tokens actually arrive."""
    splitter = SentenceSplitter()
    out = []
    for i in range(0, len(text), chunk):
        out.extend(splitter.feed(text[i:i + chunk]))
    tail = splitter.flush()
    if tail:
        out.append(tail)
    return out


# --------------------------------------------------------------- splitting ---

def test_a_rupee_figure_is_never_cut_in_half():
    """The failure this guard exists for. "₹1,06,398.02" split naively becomes
    "₹1,06,398." followed by "02" — two utterances, the first a wrong number
    said out loud to someone who cannot see the screen."""
    text = "Your maturity amount is ₹1,06,398.02. The rate is 6.25% per annum."
    assert split_all(text) == [
        "Your maturity amount is ₹1,06,398.02.",
        "The rate is 6.25% per annum.",
    ]


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 40, 1000])
def test_chunk_size_does_not_change_the_result(chunk):
    """Token boundaries are arbitrary and can land mid-number or mid-escape.
    The output must not depend on where they fall."""
    text = "FD gives 6.25%. On ₹1,00,000 that is ₹6,398.02 in a year. Worth it?"
    assert split_all(text, chunk) == [
        "FD gives 6.25%.",
        "On ₹1,00,000 that is ₹6,398.02 in a year.",
        "Worth it?",
    ]


def test_indic_sentence_marks_end_sentences():
    """`।` is the Devanagari danda (Hindi/Marathi). Tamil conventionally uses
    the ASCII stop, which the first case covers."""
    assert split_all("சேமிப்பு கணக்கு நல்லது. வட்டி 2.5% கிடைக்கும்.") == [
        "சேமிப்பு கணக்கு நல்லது.",
        "வட்டி 2.5% கிடைக்கும்.",
    ]
    assert split_all("यह खाता अच्छा है। ब्याज 2.5% मिलता है।") == [
        "यह खाता अच्छा है।",
        "ब्याज 2.5% मिलता है।",
    ]


def test_abbreviations_do_not_end_a_sentence():
    assert split_all("Deposit Rs. 5000 today. Then wait.") == [
        "Deposit Rs. 5000 today.",
        "Then wait.",
    ]


def test_a_terminator_at_the_very_end_waits_for_flush():
    """Mid-stream, a trailing "." might be a decimal point whose digits have
    not arrived. It is only safe to emit once the stream is over."""
    splitter = SentenceSplitter()
    assert splitter.feed("The rate is 6.") == []
    assert splitter.feed("25% today.") == []
    assert splitter.flush() == "The rate is 6.25% today."


def test_closing_quotes_stay_with_their_sentence():
    assert split_all('He said "yes." Then he left.') == ['He said "yes."', "Then he left."]


def test_nothing_is_lost_between_feed_and_flush():
    text = "One. Two! Three? Four"
    assert "".join(split_all(text)).replace(" ", "") == text.replace(" ", "")


# ------------------------------------------------------------ json extract ---

def test_only_the_content_field_is_ever_spoken():
    """The stream is a JSON object. Splitting its tokens directly would send
    `{"language":"Tamil"` to a speech engine."""
    doc = json.dumps({"language": "Tamil", "content_type": "text",
                      "content": "வட்டி 2.5%. நன்றி.", "confidence": 0.9},
                     ensure_ascii=False)
    extractor = JsonStringField("content")
    spoken = "".join(extractor.feed(doc[i:i + 3]) for i in range(0, len(doc), 3))
    assert spoken == "வட்டி 2.5%. நன்றி."
    assert "language" not in spoken and "{" not in spoken


def test_escapes_survive_being_split_across_chunks():
    """A chunk can end mid-\\uXXXX. Emitting the fragment would put a stray
    backslash into speech."""
    doc = '{"content": "a\\u0932b\\nc\\"d"}'
    for chunk in (1, 2, 3, 5, 999):
        extractor = JsonStringField("content")
        got = "".join(extractor.feed(doc[i:i + chunk]) for i in range(0, len(doc), chunk))
        assert got == 'aलb\nc"d', f"chunk size {chunk}"


def test_the_field_ends_at_its_closing_quote():
    """An unterminated value is still open; the closing quote ends it, and
    nothing after that is ever spoken."""
    extractor = JsonStringField("content")
    assert extractor.feed('{"content": "done.') == "done."
    assert extractor.done is False
    assert extractor.feed('", "confidence": 0.9}') == ""
    assert extractor.done is True
    # A second document on the same extractor emits nothing.
    assert extractor.feed('{"content": "more"}') == ""


def test_a_field_before_content_is_not_spoken():
    doc = '{"language": "Tamil", "content": "hello"}'
    extractor = JsonStringField("content")
    assert extractor.feed(doc) == "hello"


# ------------------------------------------------------------- end to end ---

class _FakeAdapter:
    """Emits a JSON document in small pieces, like Ollama does."""

    def __init__(self, payload: dict, chunk: int = 4):
        self._raw = json.dumps(payload, ensure_ascii=False)
        self._chunk = chunk

    def stream_structured(self, **_kwargs):
        for i in range(0, len(self._raw), self._chunk):
            yield self._raw[i:i + self._chunk], None
        yield "", {"model": "gemma4:12b", "eval_count": 42, "done": True}

    @staticmethod
    def _call_metadata(body, duration_ms):
        return {"model": body.get("model"), "duration_ms": duration_ms}


def test_sentences_are_forwarded_as_they_complete_not_at_the_end():
    sent: list[str] = []
    adapter = _FakeAdapter({"language": "English", "content_type": "text",
                            "content": "Your rate is 6.25%. That is fixed. Anything else?"})

    result = asyncio.run(stream_to_speech(
        adapter, system_prompt="s", user_prompt="u", schema={}, language="en-IN",
        sink=lambda text, _lang: sent.append(text),
    ))

    assert sent == ["Your rate is 6.25%.", "That is fixed.", "Anything else?"]
    assert [s.text for s in result.sentences] == sent
    assert all(s.forwarded for s in result.sentences)
    # The structured answer is still intact for the normal validation path.
    assert result.parsed["content_type"] == "text"
    assert result.meta["model"] == "gemma4:12b"


def test_a_trailing_partial_sentence_is_flushed():
    sent: list[str] = []
    adapter = _FakeAdapter({"language": "English", "content_type": "text",
                            "content": "Done. No full stop here"})
    asyncio.run(stream_to_speech(adapter, system_prompt="s", user_prompt="u",
                                 schema={}, sink=lambda t, _l: sent.append(t)))
    assert sent == ["Done.", "No full stop here"]


def test_a_dead_speech_service_does_not_kill_the_answer():
    """A reply that reached the user as text but not as audio is a degraded
    success. An exception here would make it nothing at all."""
    def boom(_text, _lang):
        raise ConnectionError("GPU box unreachable")

    adapter = _FakeAdapter({"language": "English", "content_type": "text",
                            "content": "First. Second."})
    result = asyncio.run(stream_to_speech(adapter, system_prompt="s", user_prompt="u",
                                          schema={}, sink=boom))

    assert result.parsed["content"] == "First. Second."
    assert len(result.sentences) == 2
    assert not any(s.forwarded for s in result.sentences)
    assert all("ConnectionError" in (s.error or "") for s in result.sentences)


def test_latency_is_recorded_per_sentence_and_increases():
    adapter = _FakeAdapter({"language": "English", "content_type": "text",
                            "content": "One. Two. Three."})
    result = asyncio.run(stream_to_speech(adapter, system_prompt="s", user_prompt="u",
                                          schema={}, sink=lambda t, _l: None))
    times = [s.elapsed_ms for s in result.sentences]
    assert len(times) == 3
    assert times == sorted(times)
    assert result.first_sentence_ms == times[0]


def test_a_slow_sink_does_not_stall_token_reading():
    """The reason any of this is async. If forwarding blocked the reader, the
    three sentences would be dispatched serially and the last one's timestamp
    would carry the full sink delay."""
    async def _run():
        def slow(_text, _lang):
            import time as _t
            _t.sleep(0.25)

        adapter = _FakeAdapter({"language": "English", "content_type": "text",
                                "content": "One. Two. Three."})
        return await stream_to_speech(adapter, system_prompt="s", user_prompt="u",
                                      schema={}, sink=slow)

    result = asyncio.run(_run())
    # Each sink call sleeps 250ms. Serial forwarding would put sentence 3 past
    # 500ms; concurrent dispatch keeps every dispatch time near zero.
    assert result.sentences[-1].elapsed_ms < 250, (
        f"forwarding blocked generation: {[s.elapsed_ms for s in result.sentences]}")


def test_a_broken_stream_is_raised_not_half_answered():
    """Retrying would replay audio the listener has already heard, so the
    caller falls back to the non-streaming path instead."""
    class _Dying:
        def stream_structured(self, **_kwargs):
            yield '{"content": "First. ', None
            raise RuntimeError("connection reset")

        @staticmethod
        def _call_metadata(body, duration_ms):
            return {}

    sent: list[str] = []
    with pytest.raises(RuntimeError, match="connection reset"):
        asyncio.run(stream_to_speech(_Dying(), system_prompt="s", user_prompt="u",
                                     schema={}, sink=lambda t, _l: sent.append(t)))
    assert sent == ["First."], "what was already spoken should have been spoken"


def test_this_side_never_calls_a_speech_service():
    """Synthesis, playback and the audio channel are the client's. An earlier
    version POSTed to a TTS endpoint, which was wrong for this architecture:
    the browser owns the speaker, so a WAV produced here had nowhere to go."""
    import ast
    import inspect

    from agent_platform.llm import speech_stream

    tree = ast.parse(inspect.getsource(speech_stream))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "requests" not in imported, f"must not make HTTP calls; imports {imported}"
    assert "httpx" not in imported and "urllib" not in imported


def test_the_sink_can_be_supplied_by_context(monkeypatch):
    """The sink is chosen at the HTTP edge and consumed six frames down,
    inside a stage shared with every other agent. A ContextVar keeps a speech
    concern out of signatures that have nothing to do with speech."""
    from agent_platform.llm import speech_stream

    seen: list[str] = []
    token = speech_stream.sentence_sink.set(lambda text, _lang: seen.append(text))
    try:
        adapter = _FakeAdapter({"language": "English", "content_type": "text",
                                "content": "One. Two."})
        result = asyncio.run(stream_to_speech(adapter, system_prompt="s",
                                              user_prompt="u", schema={}))
    finally:
        speech_stream.sentence_sink.reset(token)

    assert seen == ["One.", "Two."]
    assert all(s.forwarded for s in result.sentences)


def test_with_nobody_listening_streaming_still_runs():
    """Every non-streaming caller is in this state: sentences are split and
    timed, they simply have nowhere to go."""
    adapter = _FakeAdapter({"language": "English", "content_type": "text",
                            "content": "One. Two."})

    result = asyncio.run(stream_to_speech(adapter, system_prompt="s",
                                          user_prompt="u", schema={}))

    assert [s.text for s in result.sentences] == ["One.", "Two."]
    assert not any(s.forwarded for s in result.sentences)
    assert all(s.error == "no sink" for s in result.sentences)
    assert result.parsed["content"] == "One. Two."


def test_result_defaults_are_safe():
    empty = StreamResult()
    assert empty.first_sentence_ms is None
    assert empty.sentences == []

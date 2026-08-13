"""Sarvam AI integration.

The guarantee: this is an optional accelerant. With no key, an unreachable
host, a changed response shape or a slow reply, every path returns None and
the pipeline behaves exactly as it does without Sarvam at all. Nothing about
grounding, and nothing about answering, may depend on a third party.
"""
from __future__ import annotations

import pytest
import requests

from agent_platform.stages import pipeline_stages
from capabilities_impl import sarvam


class _Logger:
    def __init__(self):
        self.events = []

    def event(self, ctx, name, **fields):
        self.events.append((name, fields))

    def warning(self, ctx, message):
        self.events.append(("warning", {"message": message}))


class _Ctx:
    def __init__(self, raw_input):
        self.raw_input = raw_input


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.setattr(sarvam, "_cache", {})


def test_no_key_means_no_calls_and_no_failures(monkeypatch):
    """The default state of every developer machine and of CI."""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)

    def explode(*_a, **_k):
        raise AssertionError("must not reach the network without a key")

    monkeypatch.setattr(requests, "post", explode)

    assert sarvam.available() is False
    assert sarvam.identify_language("சேமிப்பு கணக்கு") is None
    assert sarvam.translate("சேமிப்பு கணக்கு") is None


def test_an_unreachable_host_is_survivable(monkeypatch):
    """This runs before the answer, so a third party being down would
    otherwise be felt on every single turn."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    def timeout(*_a, **_k):
        raise requests.Timeout("too slow")

    monkeypatch.setattr(requests, "post", timeout)
    assert sarvam.identify_language("சேமிப்பு கணக்கு") is None


def test_an_unexpected_response_shape_returns_none(monkeypatch):
    """Four things in this integration are unverified against a live key: the
    header name, two paths, and the response fields. Each one, if wrong,
    should degrade to None rather than raise."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    class _Response:
        status_code = 200
        text = '{"unexpected": true}'

        @staticmethod
        def json():
            return {"unexpected": True}

    monkeypatch.setattr(requests, "post", lambda *_a, **_k: _Response())
    assert sarvam.identify_language("சேமிப்பு கணக்கு") is None


def test_a_detected_language_is_mapped_to_our_own_codes(monkeypatch):
    """The rest of this codebase speaks bare codes — style_examples keys its
    index by "hi" and "ta" — while Sarvam speaks "hi-IN"."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"language_code": "ta-IN", "script_code": "Taml"}

    monkeypatch.setattr(requests, "post", lambda *_a, **_k: _Response())
    result = sarvam.identify_language("சேமிப்பு கணக்கு")
    assert result == {"code": "ta", "sarvam_code": "ta-IN", "name": "Tamil", "script": "Taml"}


def test_language_names_are_human_readable():
    """A prompt saying "the user is writing in ta-IN" asks the model to know a
    BCP-47 table. "Tamil" does not."""
    assert sarvam.language_name("ta-IN") == "Tamil"
    assert sarvam.language_name("ta") == "Tamil"
    assert sarvam.language_name("hi-IN") == "Hindi"
    assert sarvam.language_name("te-IN") == "Telugu"
    # Unknown codes pass through rather than being dropped or guessed at.
    assert sarvam.language_name("xx-YY") == "xx-YY"
    assert sarvam.language_name("") == ""


def test_a_declared_language_is_used_without_calling_sarvam(monkeypatch):
    """The voice client already sends `language`, and its ASR knows what it
    transcribed better than anything downstream can infer from the output.
    Asking a third party to re-derive it would be slower and worse."""
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    def explode(*_a, **_k):
        raise AssertionError("a declared language must not trigger detection")

    monkeypatch.setattr(requests, "post", explode)

    section, code = pipeline_stages._language_section(
        _Ctx({"evidence": {"question": "…", "language": "ta-IN"}}), _Logger())

    assert code == "Tamil"
    assert "Tamil" in section
    assert "ta-IN" not in section


def test_no_language_and_no_key_changes_nothing(monkeypatch):
    """The state this shipped in. Behaviour must be identical to before."""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    section, code = pipeline_stages._language_section(
        _Ctx({"evidence": {"question": "what is the FD rate?"}}), _Logger())
    assert section == ""
    assert code is None


def test_the_directive_survives_a_garbled_transcript(monkeypatch):
    """The bug this exists for: a Tamil question answered in Telugu, because
    the ASR text was mangled and the model inferred from it. The instruction
    has to explicitly outrank what the text looks like."""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    section, _code = pipeline_stages._language_section(
        _Ctx({"evidence": {"question": "தான் வேஜி", "language": "ta-IN"}}), _Logger())

    lowered = section.lower()
    assert "transcript" in lowered
    assert "do not switch to a different language" in lowered

"""Spoken-answer mode.

The guarantee: voice changes the shape of an answer and nothing else. It may
cut sections, formatting and digressions; it may not cut a figure, relax how
one is written, or reach the step that chooses tools.
"""
from __future__ import annotations

from agent_platform.stages import pipeline_stages


class _Ctx:
    def __init__(self, raw_input):
        self.raw_input = raw_input


def test_voice_is_off_unless_asked_for():
    """Opposite default to style, on purpose. Style shapes every answer unless
    switched off; voice restructures one for a channel most callers are not
    on, so only an explicit true turns it on.
    """
    for raw_input in (
        {"evidence": {"message": "hi"}},                  # the /invoke shape
        {"evidence": {"message": "hi"}, "voice": False},
        {"evidence": {"message": "hi"}, "voice": None},
        {"evidence": {"message": "hi"}, "voice": "true"},  # already coerced at the route
        {},
        None,
    ):
        assert pipeline_stages._voice_enabled(raw_input) is False, raw_input

    assert pipeline_stages._voice_enabled({"evidence": {}, "voice": True}) is True


def test_the_voice_flag_is_never_shown_to_the_model():
    """Same trap the style flag fell into: _build_text_prompt renders every
    raw_input key it does not know as routing straight into the user prompt,
    where the tool loop reads it and changes which tools it calls."""
    skill = type("_Skill", (), {"instructions_text": "be helpful", "shared_text": ""})()

    _system, user_prompt = pipeline_stages._build_text_prompt(
        skill, {"evidence": {"message": "what is the FD rate?"}, "voice": True, "style": False})

    assert "voice" not in user_prompt.lower()
    assert "style" not in user_prompt.lower()
    assert "FD rate" in user_prompt, "the actual message must still get through"


def test_the_brief_forbids_markdown_and_caps_length():
    """The two things a text-to-speech engine cannot recover from: markdown
    punctuation read out literally, and an answer too long to listen to."""
    brief = pipeline_stages._VOICE_BRIEF.lower()
    assert "no markdown" in brief
    assert "bullet" in brief and "heading" in brief
    assert "two to four sentences" in brief
    # Brevity must come out of structure, not out of facts.
    assert "do not round" in brief
    assert "caveat" in brief
    # An image payload is a raw JSON object; spoken, it is unusable.
    assert "never emit an image" in brief


def test_number_formatting_survives_the_override():
    """The brief overrides length and layout, and an earlier draft said
    "formatting" — which the model read as licence to drop Indian digit
    grouping. The same FD figure came out ₹1,06,398.02 on screen and
    ₹106,398.02 spoken, which an Indian listener hears as a hundred thousand
    rather than a lakh.
    """
    brief = pipeline_stages._VOICE_BRIEF
    assert "length and layout" in brief
    assert "formatting.**" not in brief, "the override must not extend to number formatting"
    assert "₹1,06,398.02" in brief and "₹106,398.02" in brief


def test_voice_has_the_last_word_over_style():
    """They contradict each other directly — style says "say everything you
    would have said, the same length", voice says "two to four sentences" —
    and the one that must win is the one that knows the answer is spoken.
    Position in the prompt is how that is expressed, so it is what is pinned.
    """
    import inspect

    source = inspect.getsource(pipeline_stages.reason_llm_with_tools)
    line = next(ln for ln in source.splitlines() if "answer_prompt = system_prompt" in ln)
    assert line.index("style_text") < line.index("_VOICE_BRIEF")

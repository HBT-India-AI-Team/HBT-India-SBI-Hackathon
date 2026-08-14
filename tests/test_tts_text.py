"""Cleaning a sentence before it is spoken.

Two halves. It must remove what a speech engine cannot pronounce, and it must
not touch a rupee figure — this agent's entire output is money, and a cleaner
that mangles a number is worse than no cleaner at all, because the wrong
number is then said out loud with full confidence.
"""
from __future__ import annotations

import pytest

from agent_platform.llm.tts_text import normalize_for_tts


@pytest.mark.parametrize("before, after", [
    ("- Check your balance first.", "Check your balance first."),
    ("1. Check your balance.", "Check your balance."),
    ("2) Review the alert.", "Review the alert."),
    ("• The rate is 6.5%.", "The rate is 6.5%."),
    ("**Bold** matters.", "Bold matters."),
    ("## Heading", "Heading."),
])
def test_markup_a_speech_engine_cannot_say_is_removed(before, after):
    assert normalize_for_tts(before) == after


def test_brackets_go_but_their_contents_stay():
    """"(EMI)" must become "EMI", not vanish — the word is the point."""
    assert normalize_for_tts("Your EMI (equated monthly instalment) is fixed.") == \
        "Your EMI equated monthly instalment is fixed."


@pytest.mark.parametrize("sentence", [
    # The exact figure the EMI calculator and the prose both quote.
    "Your monthly EMI would be ₹17,356.46.",
    "The FD matures at ₹1,06,398.02.",
    # A leading digit that is not a list marker: `\\d+\\.` without a required
    # space turns this into "5% is the rate", a wrong number spoken aloud.
    "8.5% is the rate on that loan.",
    "2024. That was the year the rule changed.",
    "Rs. 2 lakh is the limit.",
    "It is a senior-citizen FD at 6.75%.",
])
def test_numbers_and_figures_pass_through_untouched(sentence):
    assert normalize_for_tts(sentence) == sentence


def test_missing_punctuation_is_supplied():
    assert normalize_for_tts("The rate is 6.5%") == "The rate is 6.5%."


@pytest.mark.parametrize("sentence", [
    "இது சரியான தேர்வு.",       # Tamil, ASCII stop — conventional
    "यह सही विकल्प है।",          # Hindi, danda
    "Well, then…",
])
def test_an_existing_terminator_never_collects_a_second_one(sentence):
    """Tamil ends sentences with the ASCII full stop. `।` is the Devanagari
    danda and belongs to Hindi; it is accepted where it appears but is never
    inserted, and neither ending should be given a redundant stop."""
    assert normalize_for_tts(sentence) == sentence


def test_whitespace_and_newlines_collapse():
    assert normalize_for_tts("One   thing.\n\nThen   another.") == "One thing. Then another."


def test_a_chunk_that_was_only_markup_comes_back_empty():
    """The caller drops these rather than dispatching an empty utterance."""
    assert normalize_for_tts("**") == ""
    assert normalize_for_tts("   ") == ""
    assert normalize_for_tts("") == ""


def test_it_is_idempotent():
    """The frontend runs its own normalizeForTTS() on the Ollama fallback path.
    Both cleaners can run over the same text, so a second pass must be a no-op
    rather than, say, appending a second full stop."""
    once = normalize_for_tts("1. Your EMI (monthly) is ₹17,356.46")
    assert normalize_for_tts(once) == once
    assert once == "Your EMI monthly is ₹17,356.46."


def test_normalization_is_off_unless_voice_is_on():
    """Streaming feeds the on-screen bubble when voice is off, where markdown
    is wanted. Stripping it there would be damage, not cleanup."""
    import asyncio

    from agent_platform.llm import speech_stream
    from tests.test_streaming import _FakeAdapter

    def run(normalize):
        seen: list[str] = []
        asyncio.run(speech_stream.stream_to_speech(
            _FakeAdapter({"language": "English", "content_type": "text",
                          "content": "**Bold** one. Two."}),
            system_prompt="s", user_prompt="u", schema={},
            sink=lambda text, _lang: seen.append(text), normalize=normalize))
        return seen

    assert run(False) == ["**Bold** one.", "Two."]
    assert run(True) == ["Bold one.", "Two."]

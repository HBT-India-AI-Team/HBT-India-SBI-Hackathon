"""Cleaning one finished sentence before it goes out to be spoken.

The voice brief already tells the model not to write markdown, lists or
parentheses in spoken mode. This is the net under that, for the times it does
anyway — a model instructed not to produce bullets produces them occasionally,
and a leaked `*` is not a cosmetic defect: a speech engine either reads it out
("asterisk") or strips it and runs two sentences together. Neither is
recoverable once it is audio.

**Nothing here calls a speech service.** This is a pure string function. Speech
synthesis, playback and the audio channel belong to the client; see
speech_stream.py for why that split exists.

## What it deliberately does not touch

Money is this agent's entire output, and every character in `₹1,06,398.02`
carries meaning. So the strip lists are allow-lists of symbols that are never
part of a figure, never blanket "remove punctuation":

  - `.` `,` `%` `₹` `-` `/` `:` are left alone everywhere.
  - A list marker is only stripped at the *start* of a chunk and only when
    followed by whitespace. `8.5% interest` is digit-stop-digit with no space,
    so it cannot match. The digit run is capped at two so `2024. That year…`
    survives as well.
  - Trailing punctuation is only added when the chunk ends without any
    terminator at all.

## On Tamil sentence endings

Tamil conventionally ends sentences with the ASCII full stop. `।` (U+0964) is
the Devanagari danda — Hindi, Marathi, Sanskrit. It is *accepted* here as a
valid terminator, because a mixed-script reply may contain one and rejecting
it would append a redundant stop, but it is never *inserted* into Tamil.
"""
from __future__ import annotations

import logging
import re

from agent_platform.llm.streaming import TERMINATORS

logger = logging.getLogger(__name__)

# A leading list marker: bullet glyphs, or a short number followed by `.`/`)`.
#
# The trailing `\s+` is load-bearing. Without it `\d+\.` matches the "8." in
# "8.5% is the rate" and the sentence is spoken as "5% is the rate" -- a wrong
# number said out loud, which is the failure this whole module exists to stop.
# `{1,2}` is the second guard: no list runs to "2024.".
_LIST_MARKER = re.compile(r"^\s*(?:[-*•·‣▪]|\d{1,2}[.)])\s+")

# Brackets are removed but their contents kept: "(EMI)" must become "EMI", not
# vanish. Spoken, a bracket is either read aloud or heard as a pause in the
# wrong place.
_BRACKETS = re.compile(r"[()\[\]{}<>]")

# Symbols that are never part of a number, a name or a rupee figure, and that
# a speech engine has no way to pronounce. `#` and `~` included; `-` and `/`
# deliberately not, because "senior-citizen" and "6.5%/year" are real output.
_NON_SPEECH = re.compile(r"[#*_|~`^]")

_WHITESPACE = re.compile(r"\s+")


def normalize_for_tts(text: str) -> str:
    """Make one sentence safe to speak. Pure; safe to call on anything.

    Returns "" for input that is empty or was nothing but markup, and the
    caller is expected to drop those rather than send an empty utterance.
    """
    if not text:
        return ""

    cleaned = _LIST_MARKER.sub("", text)
    cleaned = _BRACKETS.sub("", cleaned)
    cleaned = _NON_SPEECH.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()

    # Space left behind by a removed symbol mid-phrase: "EMI , the monthly".
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)

    if not cleaned:
        return ""
    if cleaned[-1] not in TERMINATORS:
        # Only when there is no terminator at all. A chunk ending "…" or "।"
        # already ends a sentence and must not collect a second stop.
        cleaned += "."
    return cleaned


def _is_trivial(before: str, after: str) -> bool:
    """Did normalization only tidy whitespace, or did it remove content?

    Used to keep the log to the cases worth reading. Whitespace collapsing
    happens on nearly every chunk and says nothing about whether the model
    followed the brief; a stripped bullet says exactly that.
    """
    return _WHITESPACE.sub(" ", before).strip() == after


def clean_for_speech(text: str) -> str:
    """normalize_for_tts, plus a log line when the model ignored the brief.

    Separated so the pure function stays trivially testable and so the log is
    emitted once, at the single point where a sentence is finalized, rather
    than everywhere the cleaner happens to be called.

    The log is the feedback loop the prompt needs: if bullets keep appearing
    in spoken mode, that is a prompt problem this cleaner is only hiding, and
    these lines are the evidence for fixing it at the source.
    """
    cleaned = normalize_for_tts(text)
    if cleaned != text and not _is_trivial(text, cleaned):
        logger.info("normalize_for_tts changed a spoken chunk: %r -> %r", text, cleaned)
    return cleaned

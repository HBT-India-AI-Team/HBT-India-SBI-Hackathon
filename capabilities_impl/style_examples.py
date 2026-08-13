"""Retrieve vernacular style examples to shape how an answer is worded.

This is not a capability and is deliberately not registered as a tool. The
model never chooses to call it and never sees it as an option: the passages
are injected into the answer prompt by the pipeline, because "write like
this" is an instruction, not a fact the model should be able to look up.

Read alongside doc_search.py, which this mirrors. The differences are the
interesting part:

  doc_search              style_examples
  -----------             --------------
  answers the question    changes only the wording
  quoted and cited        never quoted, never cited
  nomic-embed-text        bge-m3
  query must be English   query is the user's own Devanagari

The embedding model differs because the jobs differ. Measured here, nomic is
English-first and collapses on Indic script while bge-m3 collapses on
romanized Hinglish. Document queries arrive translated into English, so nomic
is right there. Style queries are the user's untranslated Hindi against a
Hindi corpus -- the pure-Indic case, where bge-m3 measured 8/10 at rank 1
against nomic's 0/10.

Safe by absence: with no index built, `for_query` returns an empty list and
the pipeline sends the prompt it would have sent anyway. Nothing about
grounding depends on this file.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.request
from pathlib import Path

_INDEX_PATH = Path(__file__).resolve().parent / "fixtures" / "style_index.json"
_EMBED_MODEL = os.environ.get("STYLE_EMBED_MODEL", "bge-m3")

# Measured against the 454-passage transcript corpus with
# scripts/eval_style_examples.py. Re-run it after any corpus change.
#
#   on-topic floor     0.496   (weakest finance question)
#   off-topic ceiling  0.584   (best score junk achieves)
#   usable gap        -0.088
#
# The gap is NEGATIVE: no threshold both serves every real question and
# excludes every irrelevant one. "मेरे फोन की बैटरी जल्दी खत्म हो जाती है"
# scores 0.584, above six of the ten finance questions, because the corpus is
# thick with app walkthroughs that talk about phones.
#
# So this is set high rather than split down the middle. At 0.60 nothing
# off-topic clears it (ceiling 0.584) and roughly six in ten real questions
# still get examples; the rest are answered exactly as they are today. That
# asymmetry is the whole argument: a missing exemplar costs nothing, while a
# mismatched one re-voices a correct answer toward an unrelated topic. Style
# is the one part of this system allowed to do nothing.
MIN_SCORE = 0.60
DEFAULT_K = 3
MAX_K = 5

_index: dict | None = None
_index_lock = threading.Lock()
_LOADED_SENTINEL: dict = {"passages": []}


def _load_index() -> dict | None:
    global _index
    with _index_lock:
        if _index is not None:
            return None if _index is _LOADED_SENTINEL else _index
        if not _INDEX_PATH.exists():
            # Cached as a sentinel so a missing index costs one stat() for the
            # life of the process, not one per turn.
            _index = _LOADED_SENTINEL
            return None
        _index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        for passage in _index.get("passages", []):
            vector = passage["embedding"]
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            passage["embedding"] = [v / norm for v in vector]
        return _index


def _embed(text: str) -> list[float] | None:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    body = json.dumps({"model": _EMBED_MODEL, "prompt": text}).encode()
    request = urllib.request.Request(
        host + "/api/embeddings", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            vector = json.load(response).get("embedding")
    except Exception:                       # noqa: BLE001 - style is optional
        # Timeout is short and failure is silent on purpose. A slow or absent
        # embedding host must never delay or break an answer whose facts are
        # already in hand.
        return None
    if not vector:
        return None
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def language_of(text: str) -> str | None:
    """Script-based, not a language detector.

    The index is keyed by language code, but this only has to answer "is this
    Devanagari" to route Hindi. Romanized Hinglish deliberately returns None:
    the corpus is Devanagari, and matching romanized text against it retrieves
    on transliteration noise rather than register.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    devanagari = sum(1 for c in letters if _DEVANAGARI.match(c))
    return "hi" if devanagari / len(letters) > 0.3 else None


def for_query(query: str, k: int = DEFAULT_K, language: str | None = None) -> list[str]:
    """Passages whose voice the answer should borrow. Empty list means
    "change nothing", and every failure path returns exactly that.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    language = language or language_of(query)
    if language is None:
        return []

    index = _load_index()
    if index is None:
        return []

    passages = [p for p in index.get("passages", []) if p.get("language") == language]
    if not passages:
        return []

    vector = _embed(query)
    if vector is None:
        return []

    k = max(1, min(int(k) if isinstance(k, int) else DEFAULT_K, MAX_K))
    scored = []
    for passage in passages:
        score = sum(a * b for a, b in zip(vector, passage["embedding"]))
        if score >= MIN_SCORE:
            scored.append((score, passage["text"]))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [text for _, text in scored[:k]]


def as_prompt_section(examples: list[str]) -> str:
    """Wrap passages so the model treats them as tone, not as content.

    The framing carries real weight. Unlabelled, retrieved text reads as
    context to draw facts from -- which would put unverified sentences next
    to tool output and invite exactly the confusion the grounding rule
    exists to prevent.
    """
    if not examples:
        return ""
    quoted = "\n\n".join(f"    {text}" for text in examples)
    return (
        "\n\n## How a real advisor explains this in the user's language\n\n"
        "Transcribed passages of an experienced finance explainer walking "
        "someone through this topic — not viewers commenting, an advisor "
        "teaching. Take from them the **everyday vocabulary and the direct, "
        "second-person way they address the listener**.\n\n"
        # Transcripts are speech. A model matching rhythm too closely picks up
        # verbal padding that is natural aloud and tiresome written down, plus
        # whatever the captioner misheard.
        "They are transcripts of talking, so they carry filler, repetition "
        "and speech-to-text errors. Do not copy those. Take the register, "
        "write it cleanly.\n\n"
        # Measured: with examples attached, answers ran 13% shorter and shed
        # real facts -- a Rs 2,00,000 RuPay accident cover and its eligibility
        # date vanished from one answer entirely. A speaker covers one point
        # per breath, and matching that shape drops the rest.
        "**Change the wording, not the substance.** These passages are short "
        "because speech is short, not because the answer should be. Say "
        "everything you would have said — every figure, every condition, "
        "every caveat, the same structure and the same length — in their "
        "words rather than yours. Dropping a fact to sound more natural is a "
        "worse answer, not a better one.\n\n"
        # The measured failure was never a wrong number -- it was a whole
        # closing section going missing. An answer that ended by telling
        # someone to build a 3-6 month emergency fund lost that paragraph
        # entirely once it started sounding conversational, and the advice
        # was the most useful thing in it.
        "**Never drop or alter a named rule, a threshold, or a "
        "recommendation.** Things like the 50-30-20 rule, a minimum balance, "
        "an age or income limit, an eligibility cut-off, or advice to build "
        "an emergency fund must survive intact — including any closing "
        "suggestion. Rephrase them in everyday words; do not summarise them "
        "away, and do not adjust the numbers in them. \"At least 3 months of "
        "expenses\" does not become \"at least 6\", and a rupee figure "
        "attached to a recommendation stays attached to it. If an answer "
        "would be shorter, it is because a sentence got simpler, never "
        "because a point got cut.\n\n"
        # The register these passages come from ends on a punchy line, and
        # the model reproduces the habit even though no passage contains the
        # sentiment: one answer closed with "put your money in the right
        # place or you'll end up a servant of the bank". FinGuru is a bank's
        # assistant. That voice is not available to it.
        "**Do not editorialise about banks, and do not add a sign-off.** "
        "Creators end on a rhetorical flourish — a warning, a jab at banks, "
        "a call to action. Do not copy that habit. No line about being "
        "cheated, trapped, looted or made a servant of anyone, and no closing "
        "one-liner that was not answering the question. Warmth is welcome; "
        "showmanship is not. Stop when the answer is finished.\n\n"
        "They are tone, not content. They are not sources, they are not "
        "current, and nothing in them is verified. Do not quote them, cite "
        "them, or take a single fact, figure or claim from them. Every number "
        "in your answer still comes from a tool call, and every rule still "
        "comes from the documents.\n\n"
        f"{quoted}\n"
    )

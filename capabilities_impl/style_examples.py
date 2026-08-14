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

# Measured with scripts/eval_style_examples.py. Re-run it after any corpus
# change -- these numbers move, and a stale threshold is invisible.
#
# 492-passage transcript corpus (hi 363, ta 129), merged, topic-scraped and
# stripped of auto-caption markers:
#
#            floor   ceiling    gap     at 0.60
#   hi       0.546    0.575   -0.029    7/10 served, 0 junk
#   ta       0.528    0.564   -0.036    3/8  served, 0 junk
#
# The previous Hindi-only corpus measured floor 0.496 / ceiling 0.584 / gap
# -0.088 at 454 passages, so both ends improved: real questions score higher
# and junk scores lower. Stripping ">>" caption markers moved the scores
# barely at all -- bge-m3 largely ignores them -- but they were 56% of the
# Tamil passages, and the point was the text shown to the model, not the
# retrieval maths.
#
# The gap is still NEGATIVE in both languages: no threshold both serves every
# real question and excludes every irrelevant one. "मेरे फोन की बैटरी जल्दी
# खत्म हो जाती है" still scores 0.575, above three of the ten finance
# questions, because the corpus is thick with app walkthroughs about phones.
#
# So this stays set high rather than split down the middle. At 0.60 nothing
# off-topic clears it in either language; the questions that miss are
# answered exactly as they are today. That asymmetry is the whole argument: a
# missing exemplar costs nothing, while a mismatched one re-voices a correct
# answer toward an unrelated topic. Style is the one part of this system
# allowed to do nothing.
#
# 0.58 would serve 9/10 Hindi and 4/8 Tamil while still admitting no measured
# junk -- but it clears Hindi's ceiling by 0.005, which is noise on an
# eight-query off-topic set. Not worth two extra exemplars.
#
# MIN_SCORE is global. Tamil's ceiling (0.564) sits below Hindi's floor, so
# one threshold works today; a language whose junk outscores another's real
# questions is the case this design cannot express.
MIN_SCORE = 0.60
DEFAULT_K = 3
MAX_K = 5

_index: dict | None = None
_index_lock = threading.Lock()
_LOADED_SENTINEL: dict = {"passages": []}

_REGISTER_DIR = Path(__file__).resolve().parent / "fixtures" / "register"
_guides: dict[str, str] = {}
_guides_lock = threading.Lock()


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


# Whole Unicode blocks. Tamil is written as an escape because its block opens
# at U+0B80, which is unassigned and renders as nothing -- spelled literally
# the range looks like a typo and invites being "fixed".
_SCRIPTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hi", re.compile("[ऀ-ॿ]")),   # Devanagari
    ("ta", re.compile("[஀-௿]")),   # Tamil
)


def language_of(text: str) -> str | None:
    """Script-based, not a language detector.

    It only has to answer "which Indic block is this written in", because
    that is what routes a query to a corpus and to a register guide.
    Romanized text deliberately returns None: the corpora are Indic-script,
    and matching romanized text against them retrieves on transliteration
    noise rather than on register.

    Adding a language means adding its block here *and* a guide under
    fixtures/register/. Forgetting the block is silent — the query simply
    never reaches its own corpus, which is the exact shape of the
    hi_transcript key mismatch.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    for code, block in _SCRIPTS:
        if sum(1 for c in letters if block.match(c)) / len(letters) > 0.3:
            return code
    return None


def register_guide(language: str | None) -> str:
    """A hand-written note on how a language is really spoken, or "".

    The retrieval half of this module needs a corpus, and a corpus is a
    strong but slow instrument. Tamil now has one — 129 passages — but it
    clears the floor on only three of eight real questions, and those three
    are exactly its best-covered topics: insurance, pension, personal loan.
    Hindi, at 363, manages seven of ten. This is the half that fires
    regardless — short, written by hand, and held to exactly the same rule as
    the passages: it may change wording and may not introduce a fact.

    That is the division of labour worth keeping: the guide covers every
    question, the corpus deepens the ones it happens to know about.

    Guides are cached for the life of the process, like the index, so editing
    one needs a backend restart. Deleting a file turns its language off.
    """
    if not language:
        return ""
    with _guides_lock:
        if language not in _guides:
            path = _REGISTER_DIR / f"{language}.md"
            try:
                _guides[language] = path.read_text(encoding="utf-8").strip()
            except OSError:
                _guides[language] = ""
        return _guides[language]


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


# Applies to any instruction to change register, retrieved or hand-written,
# which is why it lives outside both blocks below rather than inside the
# passage framing where it started.
_SUBSTANCE_GUARDS = (
    # Measured: with examples attached, answers ran 13% shorter and shed
    # real facts -- a Rs 2,00,000 RuPay accident cover and its eligibility
    # date vanished from one answer entirely. A speaker covers one point
    # per breath, and matching that shape drops the rest.
    "**Change the wording, not the substance.** Spoken language is short "
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
)


def as_prompt_section(examples: list[str], guide: str = "") -> str:
    """Assemble whichever halves of the style layer are available.

    Two independent inputs, either of which can be empty. The guide is a
    written register note and fires whenever the script is recognised; the
    passages are retrieved and fire only above the floor. With neither, this
    returns "" and the prompt goes out unchanged.

    They are kept as separate sections rather than merged because they carry
    different authority. The guide is checked-in text someone wrote on
    purpose. The passages are scraped, unverified, and need the framing
    below to stop the model mining them for facts.
    """
    if not examples and not guide:
        return ""

    parts: list[str] = []
    if guide:
        parts.append(
            "\n\n## How this language is actually spoken about money\n\n"
            f"{guide}\n\n"
        )
        # When passages follow, their block carries these already.
        if not examples:
            parts.append(_SUBSTANCE_GUARDS)
    if examples:
        parts.append(_examples_block(examples))
    return "".join(parts)


def _examples_block(examples: list[str]) -> str:
    """Wrap passages so the model treats them as tone, not as content.

    The framing carries real weight. Unlabelled, retrieved text reads as
    context to draw facts from -- which would put unverified sentences next
    to tool output and invite exactly the confusion the grounding rule
    exists to prevent.
    """
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
        + _SUBSTANCE_GUARDS +
        "They are tone, not content. They are not sources, they are not "
        "current, and nothing in them is verified. Do not quote them, cite "
        "them, or take a single fact, figure or claim from them. Every number "
        "in your answer still comes from a tool call, and every rule still "
        "comes from the documents.\n\n"
        f"{quoted}\n"
    )

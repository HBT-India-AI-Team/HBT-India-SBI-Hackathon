"""Semantic search over the RBI document index, for questions whose answer is
a rule rather than a number.

The rate tools answer "how much"; this answers "what am I entitled to" --
deposit insurance cover, liability for a fraudulent card transaction, home
loan foreclosure charges, how to escalate a complaint. Those answers exist as
published regulator text, so the agent should quote that text rather than
recall it.

Two decisions worth knowing about:

**No router.** Every query searches all sources at once. Asking a model to
first pick a source adds a decision that can be silently wrong -- when it
picks badly you can't distinguish that from the corpus genuinely not
covering the question, and real questions ("can my bank hand my data to a
credit bureau?") legitimately span several. Ranking across everything *is*
the routing, and it costs nothing extra.

**A relevance floor.** Vector search always returns its nearest neighbours,
even when the nearest thing is irrelevant -- that is the failure mode that
turns a grounded agent back into a guessing one, because an off-topic chunk
still looks like a citation. Anything below MIN_SCORE is dropped, and a
query that clears nothing comes back empty with a reason the model can
repeat, so "we don't cover that" stays reachable.
"""
from __future__ import annotations

import json
import math
import os
import threading
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

_INDEX_PATH = Path(__file__).resolve().parent / "fixtures" / "doc_index.json"
_EMBED_MODEL = "nomic-embed-text"

# Cosine similarity below this is treated as "not actually about this".
# Calibrated by measurement, not guessed -- re-measure with
# scripts/eval_doc_search.py after any corpus or embedding-model change.
#
#   corpus    off-topic ceiling    on-topic floor    usable gap
#   90        0.520                0.687             0.167
#   360       0.547                0.633             0.086
#   462       0.547                0.662             0.115
#   470       0.547                0.586             0.039
#
# Read that last row carefully before reacting to it. The floor did NOT drop
# because the SBI product pages were added: the two weakest cases are both
# pre-existing RBI ones (the DEA Fund question at 0.586, unclaimed deposits
# at 0.597), while the weakest SBI case sits at 0.617, mid-pack. What changed
# is the measurement, not the corpus -- earlier rows scored only cases that
# land in the top-k on their first phrasing, which silently excluded the one
# case that needs a rephrase. The 470 row includes every case's raw best
# score, so it is the first honest number in the column and the rows above it
# are optimistic. Measure it the new way from here on.
#
# 0.58 stays, with 0.006 of headroom on each side, because the two errors are
# not equally bad. Too high and a real passage is dropped -- recoverable, and
# already covered: the agent is told to rephrase, and that exact case climbs
# from 0.586 to 0.657 on its second phrasing. Too low and an unrelated
# paragraph comes back looking like a citation, which nothing downstream can
# catch. Given a choice between a miss the agent retries and a wrong answer
# it states confidently, take the miss.
#
# The direction of travel still constrains growth: more chunks means more
# chances something is coincidentally close, pushing the ceiling up, while
# broader coverage adds harder questions, pulling the floor down. At this gap
# a single global threshold is close to spent. The next corpus expansion of
# any size needs a reranker or per-source thresholds, not a different number
# here -- and re-running scripts/eval_doc_search.py is not optional after it.
MIN_SCORE = 0.58

# Tuned for recall, not precision, because the model is the precision filter
# and vector search is bad at this specific job. Measured case: for "what is
# the deposit insurance cover if a bank fails?", the chunk headed "What is
# the maximum deposit amount insured by the DICGC?" -- which contains the
# ₹5,00,000 answer -- ranks about NINTH. The chunks above it are genuinely
# about deposit insurance; they just don't answer the question. Cosine
# similarity measures what a passage is ABOUT, not whether it ANSWERS, and
# no reranking of these same scores fixes that (a lexical blend makes it
# worse here -- the top wrong chunk contains every query term and the right
# one is missing two).
#
# So retrieve wide and let the model pick. The cost is prompt size and
# latency; the alternative is confidently citing the wrong paragraph.
# Raised from 8 to 10 when the corpus grew 470 -> 528 chunks. The trigger was
# a measured regression, not a hunch: "how long do Sovereign Gold Bonds run
# for" went from returning the answer to missing it entirely. The answer chunk
# had not moved much -- still raw rank 8 at 0.634, well above the floor -- but
# adding SBI's own savings-bonds page introduced a second source, and
# MAX_PER_SOURCE then held the SGB FAQ to 3 slots on the first pass and pushed
# the answer into an overflow that 8 slots no longer reached. So the
# diversification meant to stop one document crowding out others had started
# crowding out the right answer from the document that held it.
#
# Worth remembering as a pattern: after corpus growth, DEFAULT_TOP_K and
# MAX_PER_SOURCE interact. Every added source makes the first pass reserve
# more slots for breadth, leaving fewer for depth in the document that
# actually answers.
DEFAULT_TOP_K = 10
MAX_TOP_K = 12

# No single document may fill more than this many of the returned slots on
# the first pass. Without it, one verbose FAQ crowds out everything else:
# every result for "how much is my deposit insured" came from the deposit
# insurance page, which is fine there and actively wrong for a question
# spanning documents ("can my bank give my data to a credit bureau?" needs
# KYC *and* the Ombudsman scheme). Leftover slots are still filled by score,
# so this diversifies without ever returning fewer passages.
MAX_PER_SOURCE = 3

# How long before the corpus should be rebuilt. Regulator FAQs change when
# circulars are amended, and a confidently-cited superseded rule is worse
# than no citation, so the age travels with every result.
MAX_AGE_DAYS = 180

_index: dict[str, Any] | None = None
_lock = threading.Lock()


def _load_index() -> dict[str, Any] | None:
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        if not _INDEX_PATH.exists():
            return None
        _index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        for chunk in _index.get("chunks", []):
            vector = chunk["embedding"]
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            # Pre-normalise once at load: cosine similarity then reduces to a
            # dot product, which matters because this runs per request.
            chunk["embedding"] = [v / norm for v in vector]
        return _index


def _embed_query(text: str) -> list[float] | None:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    body = json.dumps({"model": _EMBED_MODEL, "prompt": text}).encode()
    request = urllib.request.Request(
        host + "/api/embeddings", data=body, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            vector = json.load(response).get("embedding")
    except Exception:
        return None
    if not vector:
        return None
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _is_mostly_non_latin(text: str) -> bool:
    """True for a query written in Devanagari, Tamil, Arabic script etc.

    Deliberately counts only letters, so "FD" and digits in an otherwise
    Tamil sentence don't disguise it as English, while romanized Hinglish
    ("bhai FD ka rate kya hai") correctly reads as Latin and is allowed
    through -- the embedding model handles romanized text far better than
    it handles other scripts.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for c in letters if ord(c) > 0x24F)
    return non_latin / len(letters) > 0.3


def _diversify(scored: list[tuple[float, dict]], top_k: int) -> list[tuple[float, dict]]:
    """Takes the best `top_k`, but no more than MAX_PER_SOURCE from any one
    document on the first pass; remaining slots are filled by score.

    Order is preserved by score throughout, so the best passage is still
    first -- this only changes *which* passages make the cut, never how they
    are ranked.
    """
    picked: list[tuple[float, dict]] = []
    per_source: dict[str, int] = {}
    overflow: list[tuple[float, dict]] = []

    for score, chunk in scored:
        source = chunk["source_id"]
        if per_source.get(source, 0) < MAX_PER_SOURCE and len(picked) < top_k:
            picked.append((score, chunk))
            per_source[source] = per_source.get(source, 0) + 1
        else:
            overflow.append((score, chunk))

    # Never return fewer than we could: a query genuinely answered by one
    # document should still get that document's next-best passages.
    for item in overflow:
        if len(picked) >= top_k:
            break
        picked.append(item)

    picked.sort(key=lambda pair: pair[0], reverse=True)
    return picked


def _age_days(index: dict[str, Any]) -> int | None:
    retrieved = index.get("retrieved_on")
    if not retrieved:
        return None
    try:
        return (date.today() - date.fromisoformat(retrieved)).days
    except ValueError:
        return None


def search(query: str, top_k: int = DEFAULT_TOP_K, source_id: str | None = None) -> dict[str, Any]:
    """Finds passages of RBI guidance relevant to `query`.

    `query` must be English -- the index is English and the embedding model
    is English-first, so a Tamil query embedded as-is matches poorly. The
    agent's instructions already require English tool arguments, which makes
    a vernacular question retrieve through its English translation.
    """
    if not isinstance(query, str) or not query.strip():
        return {"available": False, "reason": "query must be a non-empty string"}

    if _is_mostly_non_latin(query):
        # Refuse loudly rather than retrieve badly. Measured on this index, a
        # raw Tamil or Hindi question scores ~0.54 against the passage its
        # English translation matches at 0.72 -- close enough to the floor
        # that it would sometimes squeak through with the right answer and
        # sometimes return nothing, which is the worst of both. Telling the
        # model to translate turns a silent quality cliff into an
        # instruction it can act on, and it already knows to do this.
        return {
            "available": False,
            "reason": "the indexed documents are in English, so this query cannot be matched — "
                        "translate the question into English, call docs.search again with that, "
                        "and give the answer back in the user's own language",
        }

    index = _load_index()
    if index is None:
        return {
            "available": False,
            "reason": "document index not built — run scripts/build_doc_index.py",
        }

    try:
        top_k = max(1, min(int(top_k), MAX_TOP_K))
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K

    chunks = index.get("chunks", [])
    if source_id:
        chunks = [c for c in chunks if c["source_id"] == source_id]
        if not chunks:
            available = sorted({c["source_id"] for c in index.get("chunks", [])})
            return {
                "available": False,
                "reason": f"unknown source_id {source_id!r}",
                "known_sources": available,
            }

    vector = _embed_query(query.strip())
    if vector is None:
        return {"available": False, "reason": "could not embed the query (embedding model unreachable)"}

    scored = []
    for chunk in chunks:
        score = sum(a * b for a, b in zip(vector, chunk["embedding"]))
        if score >= MIN_SCORE:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    age = _age_days(index)
    stale = age is not None and age > MAX_AGE_DAYS

    if not scored:
        return {
            "available": True,
            "query": query,
            "results": [],
            "reason": (
                "no passage in the indexed RBI guidance was relevant to this question — "
                "the corpus covers deposit insurance, card transactions, housing loans "
                "and the Ombudsman scheme only"
            ),
            "sources_searched": sorted({c["source_id"] for c in chunks}),
        }

    selected = _diversify(scored, top_k)

    return {
        "available": True,
        "query": query,
        "results": [
            {
                "text": chunk["text"],
                "heading": chunk["heading"],
                "source_name": chunk["source_name"],
                "source_topic": chunk.get("topic"),
                "source_url": chunk["source_url"],
                "chunk_id": chunk["chunk_id"],
                "relevance": round(score, 3),
            }
            for score, chunk in selected
        ],
        "retrieved_on": index.get("retrieved_on"),
        "age_days": age,
        "stale": stale,
        "note": (
            "Published RBI guidance retrieved on "
            f"{index.get('retrieved_on')}. Quote it as the source of the rule. "
            "CHECK SCOPE BEFORE QUOTING: each result carries a source_topic saying which "
            "product it governs. Search matches wording, not product, so a question about "
            "one product routinely returns high-scoring passages about a different one "
            "(a personal-loan question returns housing-loan rules). Banking rules are "
            "product-specific and do not transfer. If no result's source_topic covers the "
            "product asked about, say the guidance isn't held for that product instead of "
            "quoting the nearest one."
            + (" This copy is older than it should be — tell the user to verify against rbi.org.in."
               if stale else "")
        ),
    }


def list_sources() -> dict[str, Any]:
    """What the document corpus actually covers — so the agent can say what
    it can and cannot look up instead of guessing at the boundary."""
    index = _load_index()
    if index is None:
        return {
            "available": False,
            "reason": "document index not built — run scripts/build_doc_index.py",
        }
    age = _age_days(index)
    return {
        "available": True,
        "sources": [
            {
                "source_id": s["source_id"],
                "source_name": s["source_name"],
                "topic": s["topic"],
                "source_url": s["url"],
            }
            for s in index.get("sources", [])
        ],
        "chunk_count": len(index.get("chunks", [])),
        "retrieved_on": index.get("retrieved_on"),
        "age_days": age,
        "stale": age is not None and age > MAX_AGE_DAYS,
    }

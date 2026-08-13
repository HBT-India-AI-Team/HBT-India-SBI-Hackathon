"""Document retrieval: the guards that keep it from citing the wrong thing.

Everything here runs offline (per docs/testing.md). That rules out asserting
retrieval *quality*, which needs a live embedding model — but the things most
likely to break silently aren't about quality anyway. They're the refusals:
the paths where search declines to answer instead of returning something
plausible and wrong. Those are all reachable without a network call, because
each one short-circuits before the query is ever embedded.
"""
from __future__ import annotations

import json

import pytest

import capabilities_impl  # noqa: F401 — registers the capabilities
from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.composition import load_agent
from agent_platform.stages.pipeline_stages import _TOOL_SCHEMAS
from capabilities_impl import doc_search


# -- wiring ----

def test_doc_tools_are_registered_and_schemad():
    for name in ("docs.search", "docs.list_sources"):
        assert DEFAULT_REGISTRY.has(name), f"{name} not registered"
        assert name in _TOOL_SCHEMAS, f"{name} has no tool schema"


def test_finguru_declares_the_doc_tools():
    declared = {c.name for c in load_agent("finguru").definition.capabilities}
    assert {"docs.search", "docs.list_sources"} <= declared


# -- the index itself ----

def test_index_is_built_and_every_chunk_is_attributable():
    """A chunk without a source URL can be quoted but not checked, which
    defeats the point — the agent's promise is that a rule can be traced
    back to the regulator's own page.
    """
    index = doc_search._load_index()
    assert index is not None, "run scripts/build_doc_index.py"
    assert index["chunks"], "index has no chunks"
    dimensions = index["embedding_dimensions"]
    for chunk in index["chunks"]:
        assert chunk["text"].strip()
        # Public sources carry a URL; internal documents carry an
        # internal:// reference instead. Both are traceable — what must
        # never happen is an internal policy wearing a government URL,
        # which would send a checker somewhere the claim isn't.
        assert chunk["source_url"].startswith(("http", "internal://"))
        assert chunk["source_name"]
        assert len(chunk["embedding"]) == dimensions


def test_list_sources_reports_coverage_and_age():
    result = doc_search.list_sources()
    assert result["available"] is True
    assert result["chunk_count"] > 0
    assert result["sources"]
    for source in result["sources"]:
        assert source["topic"], "a source without a topic can't tell the agent what it covers"
    assert "stale" in result


# -- the refusals: where a wrong answer would otherwise be invented ----

@pytest.mark.parametrize("query", ["", "   ", None, 42])
def test_empty_or_non_string_queries_are_refused(query):
    result = doc_search.search(query)
    assert result["available"] is False
    assert result["reason"]


@pytest.mark.parametrize("query", [
    "என் வங்கி திவாலானால் எவ்வளவு காப்பீடு?",       # Tamil
    "अगर मेरा बैंक डूब जाए तो कितना बीमा है?",        # Hindi
    "আমার ব্যাংক ব্যর্থ হলে কত বীমা?",                # Bengali
])
def test_non_latin_queries_are_refused_with_a_retry_instruction(query):
    """Measured on this index, a raw vernacular query scores ~0.54 against
    the passage its English translation matches at 0.72 — close enough to
    the floor to sometimes squeak through and sometimes return nothing.
    Refusing with an instruction the model can act on beats retrieving
    badly, so the refusal has to actually say what to do.
    """
    result = doc_search.search(query)
    assert result["available"] is False
    assert "english" in result["reason"].lower()


def test_romanized_vernacular_is_allowed_through():
    """Hinglish is Latin script, which the embedding model handles far
    better than Devanagari — so the non-Latin guard must not swallow it.
    """
    assert not doc_search._is_mostly_non_latin("bhai FD ka rate kya hai")
    assert not doc_search._is_mostly_non_latin("enna bank la deposit safe ah?")


def test_digits_and_english_acronyms_do_not_disguise_a_tamil_query():
    """The guard counts letters only. Otherwise "FD" and "2,00,000" inside
    an otherwise-Tamil sentence would tip it over into looking like English.
    """
    assert doc_search._is_mostly_non_latin("FD 2,00,000 போட்டால் எவ்வளவு கிடைக்கும்?")


def test_unknown_source_id_is_refused_and_lists_the_real_ones():
    result = doc_search.search("deposit insurance", source_id="rbi_nonexistent")
    assert result["available"] is False
    assert result["known_sources"]


# -- the relevance floor ----

def test_the_relevance_floor_sits_between_measured_off_and_on_topic_scores():
    """Calibrated, not guessed. Measured on the current 470-chunk corpus:
    off-topic queries peak at 0.547 and the weakest genuinely on-topic case
    scores 0.586, leaving 0.039 of usable gap. Both bounds have moved inward
    as the corpus grew (0.520/0.687 at 90 chunks), so re-measure with
    scripts/eval_doc_search.py after any corpus change — a floor that drifts
    out of the gap turns "we don't cover that" into a confident citation of
    an unrelated paragraph.

    The bounds here are deliberately the real measured ones rather than
    round numbers: at 0.039 of headroom this assertion is the only thing
    standing between a casual threshold tweak and silent retrieval failure.
    """
    assert 0.547 < doc_search.MIN_SCORE < 0.586  # measured at 470 chunks


def test_retrieval_favours_recall_because_the_model_is_the_precision_filter():
    """Vector similarity ranks by what a passage is ABOUT, not whether it
    ANSWERS: the chunk stating the ₹5,00,000 deposit limit ranks ~9th for
    one reasonable phrasing of the question. Returning a handful would drop
    it, so the default is deliberately wide.
    """
    assert doc_search.DEFAULT_TOP_K >= 8
    assert doc_search.MAX_TOP_K >= doc_search.DEFAULT_TOP_K


# -- per-source diversification ----

def _fake(source_id: str, score: float):
    return (score, {"source_id": source_id, "chunk_id": f"{source_id}-{score}"})


def test_one_document_cannot_fill_every_slot():
    """A verbose FAQ shouldn't crowd out the document that actually answers
    a cross-cutting question."""
    scored = [_fake("a", 0.9 - i / 100) for i in range(6)] + [_fake("b", 0.8), _fake("c", 0.7)]
    picked = doc_search._diversify(scored, 5)
    sources = [c["source_id"] for _, c in picked]
    assert sources.count("a") <= doc_search.MAX_PER_SOURCE
    assert {"b", "c"} <= set(sources)


def test_diversify_never_returns_fewer_passages_than_it_could():
    """The cap redistributes slots; it must not shrink the result. A query
    genuinely answered by one document should still get that document's
    next-best passages in the leftover slots.
    """
    scored = [_fake("a", 0.9 - i / 100) for i in range(8)]
    assert len(doc_search._diversify(scored, 6)) == 6
    assert len(doc_search._diversify(scored[:2], 6)) == 2


def test_diversify_preserves_score_order():
    scored = [_fake("a", 0.9), _fake("a", 0.85), _fake("a", 0.8), _fake("b", 0.75), _fake("c", 0.7)]
    picked = doc_search._diversify(scored, 5)
    assert [s for s, _ in picked] == sorted((s for s, _ in picked), reverse=True)


# -- cross-references ----

def test_cross_references_are_resolved_into_the_chunk_that_points_at_them():
    """FAQs say things like "documents as mentioned in the reply to Q 5
    above", and chunking severs that link -- the retrieved passage then
    names no documents while looking complete and correctly cited. The
    build inlines the referenced answer, so the pointer and the thing it
    points at travel together.

    This is the one retrieval failure the model cannot recover from by
    rephrasing: retry is triggered by passages that obviously lack the
    answer, and these look fine.
    """
    index = doc_search._load_index()
    resolved = [c for c in index["chunks"] if c.get("resolved_references")]
    assert resolved, "no cross-references resolved — did the build step regress?"
    for chunk in resolved:
        assert "[Referenced Q" in chunk["text"]


def test_a_chunk_never_resolves_a_reference_to_itself():
    """Headings start with the question's own number, so a naive scan reads
    "Q 5." as chunk 5 referring to chunk 5 and duplicates it into itself.
    """
    import re

    index = doc_search._load_index()
    for chunk in index["chunks"]:
        refs = chunk.get("resolved_references") or []
        if not refs:
            continue
        own = re.match(r"^\s*(?:Q(?:uestion)?\s*\.?\s*)?(\d+)", chunk["heading"] or "")
        if own:
            assert own.group(1) not in refs, f"{chunk['chunk_id']} references itself"


def test_kyc_document_list_is_reachable_from_the_chunk_that_cites_it():
    """The concrete case this was built for: the chunk answering "KYC for
    joint accounts" defers to Q5 for the document list, and Q5 is where the
    OVDs actually are.
    """
    index = doc_search._load_index()
    joint = [c for c in index["chunks"]
             if c["source_id"] == "rbi_kyc" and "joint account" in (c["heading"] or "").lower()]
    assert joint, "expected a KYC chunk about joint accounts"
    assert any("Voter" in c["text"] and "NREGA" in c["text"] for c in joint)


def test_instructions_tell_the_model_to_rephrase_rather_than_give_up():
    """The single highest-value retrieval fix found while building this was
    not a ranking change — it was telling the model to ask for the fact
    rather than the situation, and to search again when the passages don't
    contain the answer. Losing that line silently degrades every rule
    lookup.
    """
    text = load_agent("finguru").skills["finguru"].instructions_text
    assert "rephrase" in text.lower()
    assert "docs.search" in text

"""Vernacular style retrieval.

The guarantee under test is narrow and load-bearing: style may change how an
answer is worded and may change nothing else. Every failure path returns no
examples, and the passages never reach the step that chooses tools.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_platform.stages import pipeline_stages
from capabilities_impl import style_examples


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


def test_missing_index_returns_no_examples(monkeypatch):
    """The index is optional. Without one FinGuru answers exactly as it did
    before this existed, rather than failing or waiting on a lookup."""
    monkeypatch.setattr(style_examples, "_INDEX_PATH", Path("does-not-exist.json"))
    monkeypatch.setattr(style_examples, "_index", None)
    assert style_examples.for_query("मेरी बेटी 5 साल की है") == []


def test_unreachable_embedding_host_returns_no_examples(monkeypatch, tmp_path):
    """A slow or down embedding host must not delay an answer whose facts are
    already gathered — style is the one thing here that is allowed to be
    silently skipped."""
    index = tmp_path / "style_index.json"
    index.write_text(json.dumps({
        "passages": [{"text": "कोई बात", "language": "hi", "embedding": [1.0, 0.0]}],
    }), encoding="utf-8")
    monkeypatch.setattr(style_examples, "_INDEX_PATH", index)
    monkeypatch.setattr(style_examples, "_index", None)
    monkeypatch.setattr(style_examples, "_embed", lambda _text: None)
    assert style_examples.for_query("मेरी बेटी 5 साल की है") == []


def test_language_is_routed_by_script():
    """Only Indic script routes. Matching romanized text against an
    Indic-script corpus retrieves on transliteration noise rather than
    register, so it is routed out rather than answered badly."""
    assert style_examples.language_of("मेरी बेटी 5 साल की है") == "hi"
    assert style_examples.language_of("சேமிப்பு கணக்குக்கு வட்டி எவ்வளவு?") == "ta"
    assert style_examples.language_of("bhai FD ka rate kya hai") is None
    assert style_examples.language_of("vatti evvalavu kidaikkum") is None
    assert style_examples.language_of("what is the FD rate") is None
    assert style_examples.language_of("") is None


def test_below_floor_passages_are_dropped(monkeypatch, tmp_path):
    """Nearest-neighbour search always returns something. A passage in the
    wrong register pulls the answer somewhere worse than no passage would."""
    index = tmp_path / "style_index.json"
    index.write_text(json.dumps({"passages": [
        {"text": "close enough", "language": "hi", "embedding": [1.0, 0.0]},
        {"text": "nowhere near", "language": "hi", "embedding": [0.0, 1.0]},
    ]}), encoding="utf-8")
    monkeypatch.setattr(style_examples, "_INDEX_PATH", index)
    monkeypatch.setattr(style_examples, "_index", None)
    monkeypatch.setattr(style_examples, "_embed", lambda _text: [1.0, 0.0])

    assert style_examples.for_query("कुछ भी") == ["close enough"]


def test_a_wrong_language_index_is_not_used(monkeypatch, tmp_path):
    """Passages are filtered by language before scoring, so a Marathi corpus
    can never supply the voice for a Hindi answer."""
    index = tmp_path / "style_index.json"
    index.write_text(json.dumps({"passages": [
        {"text": "marathi passage", "language": "mr", "embedding": [1.0, 0.0]},
    ]}), encoding="utf-8")
    monkeypatch.setattr(style_examples, "_INDEX_PATH", index)
    monkeypatch.setattr(style_examples, "_index", None)
    monkeypatch.setattr(style_examples, "_embed", lambda _text: [1.0, 0.0])

    assert style_examples.for_query("मेरी बेटी 5 साल की है") == []


def test_prompt_section_frames_passages_as_tone_not_source():
    """Unlabelled retrieved text sits next to tool output and reads as
    something to draw facts from. The framing is the only thing preventing
    an unverified sentence being quoted as a fact.
    """
    section = style_examples.as_prompt_section(["एक उदाहरण"])
    lowered = section.lower()
    assert "do not quote" in lowered
    assert "not verified" in lowered or "nothing in them is verified" in lowered
    assert "tool call" in lowered
    assert "एक उदाहरण" in section
    # The corpus is an advisor teaching, not viewers commenting, and saying so
    # is what points the model at the half of the register worth copying.
    assert "advisor" in lowered
    # Transcripts are speech: filler and mis-transcription are in the source
    # and must not be matched along with the vocabulary.
    assert "filler" in lowered


def test_no_examples_produces_no_prompt_text():
    assert style_examples.as_prompt_section([]) == ""


def test_style_never_reaches_the_tool_selection_prompt(monkeypatch):
    """The architectural guarantee. Tool selection on this model is fragile
    enough already — it writes English, numeric arguments, so vernacular
    passages there are noise against a step that must not wobble. Style
    belongs only on the call that writes prose.
    """
    monkeypatch.setattr(style_examples, "for_query", lambda *_a, **_k: ["शैली का उदाहरण"])

    ctx = _Ctx({"message": "मेरी बेटी 5 साल की है"})
    section, _detail = pipeline_stages._style_section(ctx, _Logger())

    assert "शैली का उदाहरण" in section, "style section should carry the passage"

    # The tool loop is called with `system_prompt`; the answer call is given
    # `system_prompt + _style_section(...)`. Reading the source is the honest
    # check here — it is the wiring, not a value, that must not regress.
    source = Path("agent_platform/stages/pipeline_stages.py").read_text(encoding="utf-8")
    loop_call = source.split("adapter.run_tool_loop(")[1].split(")")[0]
    assert "_style_section" not in loop_call
    assert "answer_prompt" not in loop_call


def test_style_failure_is_logged_and_swallowed(monkeypatch):
    """A raising retrieval must not take the answer down with it."""
    def boom(*_a, **_k):
        raise RuntimeError("index corrupt")

    monkeypatch.setattr(style_examples, "for_query", boom)
    logger = _Logger()
    section, detail = pipeline_stages._style_section(_Ctx({"message": "नमस्ते जी कैसे हैं"}), logger)
    assert section == ""
    assert detail["applied"] is False
    assert any(name == "warning" for name, _ in logger.events)


@pytest.mark.parametrize("message", [None, "", "   ", 42])
def test_no_usable_message_means_no_style(message):
    section, detail = pipeline_stages._style_section(_Ctx({"message": message}), _Logger())
    assert section == ""
    assert detail["applied"] is False


def test_the_message_is_found_in_both_caller_shapes(monkeypatch):
    """/invoke passes fields flat; the chat route wraps them in "evidence".

    Reading only the flat shape typechecked, passed its tests, and returned
    no style on every chat turn -- the only path a user actually takes. An
    A/B run showing twelve identical answer pairs is what surfaced it.
    """
    monkeypatch.setattr(style_examples, "for_query", lambda *_a, **_k: ["उदाहरण"])
    question = "गोल्ड लोन कैसे लिया जाता है?"

    flat, _ = pipeline_stages._style_section(_Ctx({"message": question}), _Logger())
    wrapped, _ = pipeline_stages._style_section(
        _Ctx({"evidence": {"message": question, "conversation": "..."}}), _Logger())

    assert "उदाहरण" in flat
    assert "उदाहरण" in wrapped, "the chat route's shape must resolve too"


def test_the_toggle_only_turns_style_off_when_asked(monkeypatch):
    """Default-on, and off only on an explicit false.

    The Playground sends this so a reviewer can compare a styled and an
    unstyled answer in one session. Every other caller — /invoke, the embed
    page, the public API — sends nothing, and nothing has to keep meaning on:
    a new flag must not quietly change what a shipped integration gets.
    """
    monkeypatch.setattr(style_examples, "for_query", lambda *_a, **_k: ["उदाहरण"])
    question = "गोल्ड लोन कैसे लिया जाता है?"

    on, _ = pipeline_stages._style_section(_Ctx({"evidence": {"message": question}, "style": True}), _Logger())
    absent, _ = pipeline_stages._style_section(_Ctx({"evidence": {"message": question}}), _Logger())
    off, detail = pipeline_stages._style_section(
        _Ctx({"evidence": {"message": question}, "style": False}), _Logger())

    assert "उदाहरण" in on
    assert absent == on, "an absent flag must behave exactly like style: true"
    assert off == ""
    assert detail["applied"] is False


def test_the_toggle_is_never_shown_to_the_model():
    """A runtime flag in raw_input is rendered into the user prompt unless it
    is declared as routing, and this one was.

    The prompt carried a literal "style: True" line, the tool loop read it,
    and tool selection changed -- on a question where turning style *off*
    pulled in an extra tool and a figure the styled answer then "lost". Three
    runs each way, perfectly reproducible, and entirely an artefact of the
    flag being visible. Style is not allowed to reach tool selection at all,
    so this is the check that it cannot.
    """
    skill = type("_Skill", (), {"instructions_text": "be helpful", "shared_text": ""})()
    raw_input = {"evidence": {"message": "गोल्ड लोन कैसे लिया जाता है?"}, "style": False}

    _system, user_prompt = pipeline_stages._build_text_prompt(skill, raw_input)

    assert "style" not in user_prompt.lower()
    assert "गोल्ड लोन" in user_prompt, "the actual message must still get through"


def test_a_written_guide_carries_a_language_with_no_corpus(monkeypatch, tmp_path):
    """Tamil has no corpus yet, and the Hindi one that exists still only
    cleared the floor on five of twelve real questions. The guide is the half
    that fires regardless, so a language is never left with nothing.
    """
    (tmp_path / "ta.md").write_text("Write the Tamil people speak.", encoding="utf-8")
    monkeypatch.setattr(style_examples, "_REGISTER_DIR", tmp_path)
    monkeypatch.setattr(style_examples, "_guides", {})
    monkeypatch.setattr(style_examples, "for_query", lambda *_a, **_k: [])

    section, detail = pipeline_stages._style_section(
        _Ctx({"evidence": {"message": "சேமிப்பு கணக்குக்கு வட்டி எவ்வளவு?"}}), _Logger())

    assert "Write the Tamil people speak." in section
    assert detail == {"applied": True, "language": "ta", "guide": True, "examples": 0}
    # A guide is an instruction to change register, which is the thing that was
    # measured dropping facts. It must not arrive without the guard.
    assert "Change the wording, not the substance" in section


def test_a_language_with_no_guide_and_no_passages_adds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(style_examples, "_REGISTER_DIR", tmp_path)
    monkeypatch.setattr(style_examples, "_guides", {})
    monkeypatch.setattr(style_examples, "for_query", lambda *_a, **_k: [])

    section, detail = pipeline_stages._style_section(
        _Ctx({"evidence": {"message": "मेरी बेटी 5 साल की है"}}), _Logger())

    assert section == ""
    assert detail["applied"] is False
    assert "0.6" in detail["reason"], "the trace should say it was the floor, not a fault"


def test_the_guards_are_not_repeated_when_both_halves_fire():
    """Both halves want the same guard, and saying it twice in one prompt is
    how a rule starts reading as filler."""
    section = style_examples.as_prompt_section(["एक उदाहरण"], "Some written guidance.")
    assert section.count("Change the wording, not the substance") == 1
    assert "Some written guidance." in section
    assert "एक उदाहरण" in section


def test_a_nested_message_is_not_invented_from_nothing():
    assert pipeline_stages._user_message({"evidence": {}}) == ""
    assert pipeline_stages._user_message({"evidence": None}) == ""
    assert pipeline_stages._user_message(None) == ""


def _filter():
    """Load the build script's filter without importing it as a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bsi", "scripts/build_style_index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.looks_like_advice


def test_loan_touts_never_become_style_examples():
    """Comment sections are full of these, and one survived an earlier version
    of the filter. A scam solicitation reaching a prompt as "write like this"
    is the worst thing this pipeline can produce.
    """
    keep, _ = _filter()(
        "किस किस को लोन चाहिए अगर जो महाराष्ट्र से है वो प्लीज बताना मैं एक "
        "आदमी का नंबर दूंगी वो 100% दिलाते हैं बस रिकॉर्ड अच्छा होना चाहिए"
    )
    assert keep is False


def test_passages_carrying_figures_are_dropped():
    """A retrieved passage is unverified. Style must not be able to smuggle a
    number in beside real tool output."""
    # Both are comfortably over MIN_CHARS so the length gate cannot be what
    # rejects them -- otherwise this passes without testing anything.
    for claim in ("PPF पर 7.1% ब्याज मिलता है और यह दर हर तिमाही में बदलती रहती है "
                  "इसलिए खाता खोलने से पहले एक बार बैंक से पूछ जरूर लेना चाहिए",
                  "इसमें आपको ₹2,00,000 तक का कवर मिल जाता है वो भी बहुत ही कम पैसे "
                  "में और प्रीमियम सीधे आपके खाते से हर साल कट जाता है समझ लीजिए"):
        keep, why = _filter()(claim)
        assert keep is False, f"claim survived the filter: {claim}"
        assert why == "carries a figure", f"rejected for {why!r}, not the figure"


def test_romanized_passages_are_dropped():
    """Only Devanagari queries reach the index, so a romanized passage could be
    embedded but never matched — keeping it would only inflate the count."""
    keep, why = _filter()(
        "Personal loan unko milta hai jinki bank details badhiya ho, option "
        "personal loan ka unke phone mein hi aata hai bhai dekh lena"
    )
    assert keep is False
    assert why == "not Devanagari"

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


def test_flags_are_read_at_whichever_level_the_caller_nests_them():
    """Three shapes are live in production and all three are legitimate.

    The voice client puts its flags *inside* `evidence`, beside the question.
    Reading only the top level meant its `voice: true` did nothing at all --
    set, sent, and read at a level it was never at, with no error anywhere.
    """
    voice_client = {"evidence": {"question": "…", "style": True, "voice": True, "language": "ta"}}
    our_chat_route = {"evidence": {"message": "…"}, "voice": True, "style": False}
    flat_invoke = {"question": "…", "voice": True}

    for raw_input in (voice_client, our_chat_route, flat_invoke):
        assert pipeline_stages._voice_enabled(raw_input) is True, raw_input

    assert pipeline_stages._style_enabled(voice_client) is True
    assert pipeline_stages._style_enabled(our_chat_route) is False


def test_the_language_is_found_under_either_name_at_either_level():
    """`language` is what the client sends. `lang` is accepted too, because
    when asked which one their client used, the frontend team did not know --
    and being wrong here fails in two directions at once, per the test below.
    """
    for key in ("language", "lang"):
        assert pipeline_stages._request_flag(
            {"evidence": {"question": "…", key: "ta"}}, *pipeline_stages._LANGUAGE_KEYS) == "ta"
        assert pipeline_stages._request_flag(
            {"evidence": {"question": "…"}, key: "ta"},
            *pipeline_stages._LANGUAGE_KEYS) == "ta"


def test_a_routing_key_is_never_shown_to_the_model_as_content():
    """The reason every spelling must be listed, not just the one we read.

    _build_text_prompt renders every key it does not recognise straight into
    the user prompt. `lang` was unlisted, so a client sending it got the
    language ignored *and* a literal "lang: ta" line appended to its own
    question -- one missing string breaking two things, with no error. The
    same omission for `style` moved tool selection, reproducibly.
    """
    class _Skill:
        instructions_text = "instructions"
        shared_text = ""
        task_prompt_text = ""

    raw_input = {"evidence": {"question": "FD rate enna?", "style": True,
                              "voice": True, "language": "ta", "lang": "ta"}}
    _, user_prompt = pipeline_stages._build_text_prompt(_Skill(), raw_input)

    assert user_prompt == "evidence: {'question': 'FD rate enna?'}"
    for routing_key in ("language", "lang", "style", "voice"):
        assert routing_key not in user_prompt


def test_language_codes_the_client_actually_sends_become_names():
    """"The user is writing in ta" asks the model to know a code table.
    "Tamil" does not. Bare two-letter codes are what the client sends."""
    from capabilities_impl import sarvam

    assert sarvam.language_name("en") == "English"
    assert sarvam.language_name("ta") == "Tamil"
    assert sarvam.language_name("hi") == "Hindi"
    # Both separators, because Android's Locale.toString() gives "ta_IN" and
    # the web platform gives "ta-IN".
    assert sarvam.language_name("ta-IN") == "Tamil"
    assert sarvam.language_name("ta_IN") == "Tamil"


class TestReplyLanguageIsChecked:
    """Declaring a language asks for one; this verifies one arrived.

    Measured on gemma4:12b: a Tamil question, with language "ta" sent, read
    and pinned, still came back written in Telugu — the exact failure the
    declaration exists to prevent. The caller cannot detect it, and a user
    reading an answer in a language they do not speak has been given nothing.
    """

    def test_a_different_indic_script_is_caught(self):
        telugu = "ప్రస్తుతం SBIలో FD వడ్డీ రేట్లు మారుతుంటాయి"
        assert pipeline_stages._wrong_script(telugu, "Tamil") == "Telugu"
        # The name and the bare code both resolve: _language_section returns
        # the display name only when Sarvam imports, the code otherwise.
        assert pipeline_stages._wrong_script(telugu, "ta") == "Telugu"
        assert pipeline_stages._wrong_script("वर्तमान में SBI में", "Tamil") == "Devanagari"

    def test_english_inside_a_correct_answer_is_not_a_mismatch(self):
        """A right answer here is full of English — "FD", "interest rate",
        bank names. A share-of-characters test either catches nothing or
        rejects good answers, which is why this compares Indic scripts to
        each other instead."""
        assert pipeline_stages._wrong_script(
            "SBI-ல FD ரேட் இப்போ 6.25% (interest rate) இருக்கு", "Tamil") is None

    def test_it_declines_to_judge_what_it_cannot(self):
        """Silence beats a wrong verdict: a romanized-Tamil reply and an
        English one are both legitimate, and neither has an Indic script to
        measure."""
        for content, language in (
            ("FD rate ippo 6.25% irukku", "Tamil"),      # romanized, no Indic
            ("The current FD rate is 6.25%.", "English"),
            ("anything at all", None),                    # nothing declared
            ("", "Tamil"),
        ):
            assert pipeline_stages._wrong_script(content, language) is None


def test_the_message_is_found_under_either_name():
    """`message` is what our chat route calls it, `question` is what the
    voice client calls it. Neither is more correct and neither is ours to
    rename, so both resolve."""
    assert pipeline_stages._user_message({"evidence": {"question": " ask "}}) == "ask"
    assert pipeline_stages._user_message({"evidence": {"message": "ask"}}) == "ask"
    assert pipeline_stages._user_message({"question": "ask"}) == "ask"
    assert pipeline_stages._user_message({"evidence": {"question": "   "}}) == ""


def test_nested_flags_do_not_reach_the_prompt_either():
    """Scrubbing only the top level left "'voice': True, 'style': True" sitting
    inside the rendered evidence dict, where the model reads it as something
    the user said."""
    skill = type("_Skill", (), {"instructions_text": "be helpful", "shared_text": ""})()
    body = {"evidence": {"question": "what is the FD rate?", "style": True,
                         "voice": True, "language": "ta"}}

    _system, user_prompt = pipeline_stages._build_text_prompt(skill, body)

    assert "'voice'" not in user_prompt and "'style'" not in user_prompt
    assert "FD rate" in user_prompt
    # `language` joined them once it became something we act on rather than
    # inert context: it is now a prompt directive of its own, and leaving it
    # in the rendered evidence as well says the same thing twice, in two
    # voices, one of which reads as the user's.
    assert "'language'" not in user_prompt


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

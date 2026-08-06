from agent_platform.skills import output_validator

CITATION_KEYS = {"bureau.score": 720, "financials.dscr": 1.3}


def _valid_output(**overrides):
    base = {
        "summary": "ok",
        "strengths": [{"point": "good score", "evidence_key": "bureau.score"}],
        "risks": [{"point": "thin dscr", "evidence_key": "financials.dscr"}],
        "next_best_action": "call the customer",
        "product_rationale": {},
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def test_valid_output_passes_unchanged():
    needs_retry, cleaned, issues = output_validator.validate_and_clean(
        _valid_output(), CITATION_KEYS
    )
    assert needs_retry is False
    assert len(cleaned["strengths"]) == 1
    assert len(cleaned["risks"]) == 1
    assert issues == []


def test_forbidden_field_is_stripped_not_fatal():
    output = _valid_output(decision="QUALIFIED")
    needs_retry, cleaned, issues = output_validator.validate_and_clean(output, CITATION_KEYS)
    assert needs_retry is False
    assert "decision" not in cleaned
    assert any("forbidden" in i for i in issues)


def test_missing_required_field_needs_retry():
    output = _valid_output()
    del output["next_best_action"]
    needs_retry, _cleaned, issues = output_validator.validate_and_clean(output, CITATION_KEYS)
    assert needs_retry is True
    assert any("missing required field" in i for i in issues)


def test_confidence_out_of_range_needs_retry():
    output = _valid_output(confidence=1.5)
    needs_retry, _cleaned, issues = output_validator.validate_and_clean(output, CITATION_KEYS)
    assert needs_retry is True
    assert any("confidence" in i for i in issues)


def test_ungrounded_citation_is_dropped_not_fatal():
    output = _valid_output(
        strengths=[
            {"point": "real", "evidence_key": "bureau.score"},
            {"point": "hallucinated", "evidence_key": "made.up.field"},
        ]
    )
    needs_retry, cleaned, issues = output_validator.validate_and_clean(output, CITATION_KEYS)
    assert needs_retry is False
    assert len(cleaned["strengths"]) == 1
    assert cleaned["strengths"][0]["point"] == "real"
    assert any("dropped ungrounded" in i for i in issues)


def test_all_citations_ungrounded_needs_retry():
    output = _valid_output(
        strengths=[{"point": "x", "evidence_key": "nope"}],
        risks=[{"point": "y", "evidence_key": "also_nope"}],
    )
    needs_retry, _cleaned, issues = output_validator.validate_and_clean(output, CITATION_KEYS)
    assert needs_retry is True
    assert any("every citation was ungrounded" in i for i in issues)


def test_deterministic_fallback_shape():
    rule_results = {
        "composite": {"value": 42.0},
        "products": [{"id": "P1", "name": "Product One"}],
    }
    fallback = output_validator.deterministic_fallback(rule_results)
    assert fallback["degraded"] is True
    assert 0.0 <= fallback["confidence"] <= 1.0
    assert "Product One" in fallback["next_best_action"]

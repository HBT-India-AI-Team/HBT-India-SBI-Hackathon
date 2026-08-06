from agent_platform.skills import rules_engine


def test_get_field_dotted_path():
    evidence = {"bureau": {"score": 720}}
    assert rules_engine.get_field(evidence, "bureau.score") == 720
    assert rules_engine.get_field(evidence, "bureau.missing") is None
    assert rules_engine.get_field(evidence, "missing.section") is None


def test_evaluate_gates_picks_most_severe_failure():
    evidence = {"a": 1, "b": 2}
    gates_config = {
        "gates": [
            {
                "id": "SOFT",
                "description": "soft gate",
                "field": "a",
                "operator": "gte",
                "value": 100,
                "on_fail": {"decision": "NEEDS_HUMAN_REVIEW", "reason": "soft fail"},
            },
            {
                "id": "HARD",
                "description": "hard gate",
                "field": "b",
                "operator": "gte",
                "value": 100,
                "on_fail": {"decision": "NOT_QUALIFIED", "reason": "hard fail"},
            },
        ]
    }
    result = rules_engine.evaluate_gates(evidence, gates_config)
    assert len(result["gates"]) == 2
    assert len(result["failures"]) == 2
    assert result["forced_decision"]["decision"] == "NOT_QUALIFIED"
    assert result["forced_decision"]["gate_id"] == "HARD"


def test_evaluate_gates_all_pass_no_forced_decision():
    evidence = {"a": 200}
    gates_config = {
        "gates": [
            {
                "id": "G1", "description": "", "field": "a", "operator": "gte", "value": 100,
                "on_fail": {"decision": "NOT_QUALIFIED", "reason": "fail"},
            }
        ]
    }
    result = rules_engine.evaluate_gates(evidence, gates_config)
    assert result["forced_decision"] is None
    assert result["gates"][0]["passed"] is True


def test_score_category_weighted_average():
    evidence = {"x": 10, "y": 0}
    category_config = {
        "factors": [
            {"id": "X", "field": "x", "weight": 0.5,
             "bands": [{"min": 10, "score": 100}, {"min": -100, "score": 0}]},
            {"id": "Y", "field": "y", "weight": 0.5,
             "bands": [{"min": 10, "score": 100}, {"min": -100, "score": 0}]},
        ]
    }
    result = rules_engine.score_category(evidence, category_config)
    assert result["value"] == 50.0
    assert result["factors"][0]["band_score"] == 100
    assert result["factors"][1]["band_score"] == 0


def test_score_category_missing_field_uses_lowest_band():
    evidence = {}
    category_config = {
        "factors": [
            {"id": "MISSING", "field": "not.present", "weight": 1.0,
             "bands": [{"min": 10, "score": 100}, {"min": -100, "score": 5}]},
        ]
    }
    result = rules_engine.score_category(evidence, category_config)
    assert result["factors"][0]["band_score"] == 5


def test_compute_composite_weighted_sum():
    category_scores = {"cat1": {"value": 80.0}, "cat2": {"value": 40.0}}
    composite_config = {
        "weights": {"cat1": 0.75, "cat2": 0.25},
        "thresholds": {"qualified_min": 75, "conditional_min": 55},
    }
    result = rules_engine.compute_composite(category_scores, composite_config)
    assert result["value"] == 70.0  # 80*0.75 + 40*0.25


def test_evaluate_products_ranks_by_specificity():
    evidence = {"industry": "manufacturing", "growth": 20}
    product_config = {
        "products": [
            {"id": "FALLBACK", "name": "Fallback", "reason": "", "when": []},
            {"id": "SPECIFIC", "name": "Specific", "reason": "",
             "when": [
                 {"field": "industry", "operator": "eq", "value": "manufacturing"},
                 {"field": "growth", "operator": "gte", "value": 10},
             ]},
            {"id": "NO_MATCH", "name": "No match", "reason": "",
             "when": [{"field": "industry", "operator": "eq", "value": "retail"}]},
        ]
    }
    matches = rules_engine.evaluate_products(evidence, product_config)
    ids = [m["id"] for m in matches]
    assert ids == ["SPECIFIC", "FALLBACK"]
    assert "NO_MATCH" not in ids


def test_run_all_skips_products_when_gate_forces_not_qualified():
    evidence = {"gate_field": 0, "score_field": 100, "industry": "manufacturing"}
    rules = {
        "gates": {
            "gates": [
                {"id": "G", "description": "", "field": "gate_field", "operator": "gte", "value": 1,
                 "on_fail": {"decision": "NOT_QUALIFIED", "reason": "fail"}}
            ]
        },
        "factors": {
            "categories": {
                "cat": {"factors": [
                    {"id": "F", "field": "score_field", "weight": 1.0,
                     "bands": [{"min": 0, "score": 100}]}
                ]}
            }
        },
        "composite": {
            "weights": {"cat": 1.0},
            "thresholds": {"qualified_min": 75, "conditional_min": 55},
        },
        "product_fit": {
            "products": [{"id": "P", "name": "P", "reason": "", "when": []}]
        },
    }
    result = rules_engine.run_all(evidence, rules)
    assert result["gates"]["forced_decision"]["decision"] == "NOT_QUALIFIED"
    assert result["products"] == []

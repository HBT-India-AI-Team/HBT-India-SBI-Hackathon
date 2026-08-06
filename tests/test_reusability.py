"""Proves the runtime claim the whole platform is built on: a second agent,
defined purely as agents/echo_probe/agent.yaml + skills_library/echo_probe/,
runs through the identical AgentLoader and pipeline executor as
lead_qualification — same runtime, different config. No platform code is
specific to either agent.
"""
from agent_platform.composition import list_agents, load_agent
from agent_platform.runtime.executor import invoke_agent


def test_second_agent_is_discoverable():
    assert "lead_qualification" in list_agents()
    assert "echo_probe" in list_agents()


def test_second_agent_has_its_own_skill_and_shorter_pipeline():
    bundle = load_agent("echo_probe")
    assert bundle.skills["echo_probe"].skill_id == "echo_probe"
    assert bundle.definition.pipeline == ["load_input", "explain"]


def test_second_agent_runs_through_the_same_executor():
    ctx = invoke_agent("echo_probe", {"message": "hello"})
    assert ctx.error is None
    assert [sr.stage for sr in ctx.stage_results] == ["load_input", "explain"]
    assert ctx.explanation["agent_id"] == "echo_probe"
    assert ctx.explanation["input_summary"] == {"message": "hello"}
    # this agent's pipeline never calls evaluate_rules/decide — those fields
    # stay empty, proving nothing lead_qualification-specific leaked in
    assert ctx.decision is None
    assert ctx.rule_results == {}


def test_second_agent_input_validation_uses_its_own_schema():
    ctx = invoke_agent("echo_probe", {})  # missing required 'message'
    assert ctx.error is not None
    assert ctx.error["stage"] == "load_input"
    assert "message" in ctx.error["message"]

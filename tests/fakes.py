"""Shared LLM fakes for tests that need reason_llm to succeed without a
live Ollama call. FakeAdapter reads the schema's `required` list to decide
which fields to fill, so one fake works for every skill's output contract
(lead_qualification, lead_discovery, proposal) rather than needing a
separate fake per agent.
"""
from agent_platform.llm import OllamaError


class FakeAdapter:
    def __init__(self, *args, dynamic_skill_ids=(), **kwargs):
        # dynamic_skill_ids: what run_tool_loop should report as "loaded",
        # simulating a model that called load_skill for each of these.
        self.dynamic_skill_ids = list(dynamic_skill_ids)

    def generate_structured(self, *, system_prompt, user_prompt, schema, temperature=0.0):
        required = schema.get("required", [])
        output = {}
        for field in required:
            if field == "confidence":
                output[field] = 0.9
            elif field in ("strengths", "risks"):
                output[field] = []
            elif field == "product_rationale":
                output[field] = {}
            else:
                output[field] = f"fake {field}"
        metadata = {"model": "fake-model", "duration_ms": 1.0,
                    "prompt_tokens": 10, "completion_tokens": 10}
        return output, metadata

    def run_tool_loop(self, *, system_prompt, user_prompt, tools, resolve_tool, max_turns=4, temperature=0.0):
        calls = []
        for skill_id in self.dynamic_skill_ids:
            resolve_tool("load_skill", {"skill_id": skill_id})
            calls.append({"name": "load_skill", "arguments": {"skill_id": skill_id}})
        return calls


class FailingAdapter:
    def __init__(self, *args, **kwargs):
        pass

    def generate_structured(self, *args, **kwargs):
        raise OllamaError("simulated LLM outage")

    def run_tool_loop(self, *args, **kwargs):
        raise OllamaError("simulated LLM outage")

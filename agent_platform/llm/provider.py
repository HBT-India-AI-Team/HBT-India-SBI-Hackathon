"""The interface every stage codes against. Swapping models or providers
(a different Ollama model, a hosted model, a future provider) means writing
a new class with this shape and pointing agent.yaml's `llm.provider` at it
— no stage changes.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class LLMProvider(Protocol):
    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Returns (parsed_json_output, call_metadata). call_metadata carries
        model name, duration_ms, token counts — whatever the caller needs
        to log, independent of the parsed content.
        """
        ...

    def run_tool_loop(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        resolve_tool: Callable[[str, dict[str, Any]], str],
        max_turns: int = 4,
        temperature: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Bounded tool-calling loop: the model may call any of `tools` zero,
        one, or several times (including several in the same turn);
        `resolve_tool(name, arguments)` is called for each and its return
        value fed back as that call's tool-result message. Stops once a turn
        produces no tool calls, or `max_turns` is reached. Returns every
        `{"name": ..., "arguments": ...}` call made, in order — the caller
        decides what those calls mean (e.g. "load this skill"); this method
        never runs a final structured-output call itself.
        """
        ...

"""A minimal, MCP-shaped tool interface.

Today, capabilities_impl/ registers plain Python functions here (backed by
JSON fixtures). Each is invoked by name, exactly like an MCP tool call would
be: `registry.invoke("lead_data.get_lead", lead_id="SME-1001")`. When a real
MCP server exists, swap the registration in capabilities_impl/__init__.py to
call out over MCP instead — no agent or stage code changes, since stages
only ever call `registry.invoke(name, **kwargs)`.
"""
from __future__ import annotations

from typing import Any, Callable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, fn: Callable[..., dict[str, Any]],
                 description: str = "") -> None:
        self._tools[name] = fn
        self._descriptions[name] = description

    def invoke(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"No tool registered under '{name}'")
        return self._tools[name](**kwargs)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": n, "description": d} for n, d in self._descriptions.items()]


DEFAULT_REGISTRY = ToolRegistry()

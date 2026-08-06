"""Extension points for MCP and A2A — NOT implemented in this demo.

These Protocols exist so the shape of the future integration is documented
and reviewable now, without pulling in an MCP/A2A dependency or writing
functional networking code before there is a real server/bus to talk to.

When a real MCP server is available: implement MCPToolClient, register its
`call_tool` under the same names ToolRegistry uses today (see tool.py), and
no stage code changes.

When a real A2A bus is available: implement A2AClient and add an
"a2a_delegate" stage that calls it; the pipeline executor in
runtime/pipeline.py already runs any stage name an agent.yaml declares, so
this is additive, not a rewrite.
"""
from __future__ import annotations

from typing import Any, Protocol


class MCPToolClient(Protocol):
    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...


class A2AClient(Protocol):
    def discover_agents(self) -> list[dict[str, Any]]:
        ...

    def delegate(self, agent_id: str, task: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_status(self, task_id: str) -> dict[str, Any]:
        ...

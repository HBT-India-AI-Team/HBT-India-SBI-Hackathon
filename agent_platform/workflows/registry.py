"""Workflow registry — mirrors STAGE_REGISTRY's pattern (agent_platform/
runtime/pipeline.py) for consistency, but for whole workflows rather than
single stages. A workflow is a plain Python function `(raw_input,
correlation_id) -> dict`; registering one is a config-free, code-only step
because a workflow genuinely is orchestration logic (branching, calling
several agents), not declarative data the way an agent is.
"""
from __future__ import annotations

from typing import Callable

WorkflowFn = Callable[[dict, str | None], dict]

WORKFLOW_REGISTRY: dict[str, WorkflowFn] = {}


def register_workflow(name: str) -> Callable[[WorkflowFn], WorkflowFn]:
    def _decorator(fn: WorkflowFn) -> WorkflowFn:
        WORKFLOW_REGISTRY[name] = fn
        return fn

    return _decorator


def list_workflows() -> list[str]:
    return sorted(WORKFLOW_REGISTRY.keys())

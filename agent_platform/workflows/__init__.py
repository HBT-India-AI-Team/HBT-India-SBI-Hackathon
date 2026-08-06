from .registry import WORKFLOW_REGISTRY, list_workflows, register_workflow

# Importing each workflow module registers it (side effect of the
# @register_workflow decorator), same pattern as agent_platform/stages/__init__.py.
from . import commercial_leadgen_demo  # noqa: F401,E402
from . import agent_router  # noqa: F401,E402


def run_workflow(workflow_id: str, raw_input: dict, correlation_id: str | None = None) -> dict:
    if workflow_id not in WORKFLOW_REGISTRY:
        raise KeyError(f"No workflow registered under '{workflow_id}'")
    return WORKFLOW_REGISTRY[workflow_id](raw_input, correlation_id)


__all__ = ["WORKFLOW_REGISTRY", "list_workflows", "register_workflow", "run_workflow"]

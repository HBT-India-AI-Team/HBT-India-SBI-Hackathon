"""Command-line entry point for running any agent (or workflow) registered
with the platform. Loads env vars, wires the mock capability
implementations into the shared ToolRegistry, then delegates to
agent_platform.runtime.executor / agent_platform.workflows — the exact same
entry points the FastAPI backend uses.

Usage:
    python cli.py list-agents
    python cli.py run lead_qualification --lead-id SME-1001
    python cli.py run lead_discovery --input '{"industry":"manufacturing","location":"Chennai"}'
    python cli.py run proposal --input-file examples/proposal_request.json
    python cli.py list-workflows
    python cli.py workflow commercial_leadgen_demo --input examples/discovery_request.json
    python cli.py show-run <run_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "backend" / ".env")

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.composition import list_agents
from agent_platform.explainability import render_markdown
from agent_platform.runtime.executor import invoke_agent
from agent_platform.state import get_run
from agent_platform.workflows import list_workflows, run_workflow


def _resolve_input(args: argparse.Namespace) -> dict:
    if getattr(args, "lead_id", None):
        return {"lead_id": args.lead_id}
    if args.input:
        return json.loads(args.input)
    if args.input_file:
        return json.loads(Path(args.input_file).read_text(encoding="utf-8"))
    print("error: provide --lead-id, --input '<json>', or --input-file <path>", file=sys.stderr)
    sys.exit(2)


def cmd_list_agents(_args: argparse.Namespace) -> None:
    agents = list_agents()
    if not agents:
        print("No agents registered under agents/.")
        return
    for agent_id in agents:
        print(agent_id)


def cmd_list_workflows(_args: argparse.Namespace) -> None:
    workflows = list_workflows()
    if not workflows:
        print("No workflows registered.")
        return
    for workflow_id in workflows:
        print(workflow_id)


def cmd_run(args: argparse.Namespace) -> None:
    raw_input = _resolve_input(args)
    ctx = invoke_agent(args.agent_id, raw_input)

    print()
    print(render_markdown(ctx.explanation))
    print()
    print(f"(full run record: runs/{ctx.run_id}.json)")

    if ctx.error:
        sys.exit(1)


def cmd_workflow(args: argparse.Namespace) -> None:
    raw_input = _resolve_input(args)
    result = run_workflow(args.workflow_id, raw_input)

    print()
    print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "FAILED":
        sys.exit(1)


def cmd_show_run(args: argparse.Namespace) -> None:
    record = get_run(args.run_id)
    if record is None:
        print(f"No run found with id '{args.run_id}'", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(record, indent=2, default=str))
    else:
        print(render_markdown(record["explanation"]))


def _add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lead-id", help="shorthand for --input '{\"lead_id\": \"...\"}'")
    parser.add_argument("--input", help="inline JSON input")
    parser.add_argument("--input-file", help="path to a JSON file of input")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reusable Agent Runtime CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-agents", help="List all registered agents").set_defaults(func=cmd_list_agents)
    subparsers.add_parser("list-workflows", help="List all registered workflows").set_defaults(func=cmd_list_workflows)

    run_parser = subparsers.add_parser("run", help="Invoke an agent")
    run_parser.add_argument("agent_id")
    _add_input_args(run_parser)
    run_parser.set_defaults(func=cmd_run)

    workflow_parser = subparsers.add_parser("workflow", help="Invoke a workflow")
    workflow_parser.add_argument("workflow_id")
    _add_input_args(workflow_parser)
    workflow_parser.set_defaults(func=cmd_workflow)

    show_parser = subparsers.add_parser("show-run", help="Show a past run's explanation")
    show_parser.add_argument("run_id")
    show_parser.add_argument("--json", action="store_true")
    show_parser.set_defaults(func=cmd_show_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

"""Interactive calculators the chat can open inline — EMI, FIRE.

A calculator is a thin wrapper over a capability that already exists. It
carries no maths of its own: `emi_calculator` computes by calling
`finance.calculate_emi`, the same registered capability the agent calls when
it answers the question in prose.

**That is the whole design.** The alternative — a formula stored per tool and
evaluated in the browser — was tried first and is worse in a specific way:
the calculator and the sentence above it would be two implementations of the
same arithmetic, and the moment they disagree the user is looking at a
contradiction with no way to tell which is right. Reusing the capability
makes disagreement impossible, and the capability is already tested.

It also means a calculator cannot invent a number the agent could not have
stated, which is the rule the rest of this system is built on.

Saved instances are keyed by `user_id`, matching the chat session index in
agent_platform/runtime/chat_store.py, so "my calculators" means the same
person in both places.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "tool_registry.db"

# tool_id -> definition. Seeded into the table on first use, and re-synced on
# every init so editing this file is enough to change a calculator. The table
# exists for saved user instances and for tools added at runtime; it is not
# the source of truth for these two.
#
# `arg_map` translates the widget's own input keys into the capability's
# parameter names. They differ on purpose: a form field wants "rate", the
# capability wants "annual_rate_percent" because it is unambiguous at a call
# site. Neither should have to bend to the other.
_BUILTIN: dict[str, dict[str, Any]] = {
    "emi_calculator": {
        "name": "EMI Calculator",
        "capability": "finance.calculate_emi",
        "inputs": [
            {"key": "principal", "label": "Loan amount", "type": "number", "prefix": "₹", "min": 0},
            {"key": "rate", "label": "Interest rate", "type": "number", "suffix": "% p.a.", "min": 0, "step": 0.05},
            {"key": "months", "label": "Tenure", "type": "number", "suffix": "months", "min": 1, "step": 1},
        ],
        "arg_map": {"principal": "principal", "rate": "annual_rate_percent",
                    "months": "tenure_months"},
        "result_key": "emi",
        "output_label": "Monthly EMI",
        "output_prefix": "₹",
    },
    "fire_calculator": {
        "name": "FIRE Planner",
        "capability": "money.sip_projection",
        "inputs": [
            {"key": "monthly_investment", "label": "Monthly investment", "type": "number",
             "prefix": "₹", "min": 0},
            {"key": "annual_return", "label": "Expected return", "type": "number",
             "suffix": "% p.a.", "min": 0, "step": 0.5},
            {"key": "years", "label": "Years", "type": "number", "suffix": "years", "min": 1, "step": 1},
        ],
        "arg_map": {"monthly_investment": "monthly_investment",
                    "annual_return": "annual_return_percent", "years": "years"},
        "result_key": "projected_value",
        "output_label": "Projected corpus",
        "output_prefix": "₹",
    },
}


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Transaction *and* close.

    `with sqlite3.connect(...)` commits but does not close — the connection
    and its file handle stay open until garbage collection. On a long-running
    server that leaks handles and keeps the database file locked, which is how
    this was found: the file could not be deleted while the backend ran.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Bumped whenever the seeded schema changes shape. The tables hold generated
# definitions and prototype state, so a mismatch is rebuilt rather than
# migrated -- there is nothing here worth a migration script yet, and a
# half-migrated table is worse than a rebuilt one.
_SCHEMA_VERSION = 2


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    return int(version or 0) != _SCHEMA_VERSION


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        if _needs_rebuild(conn):
            # v1 keyed saved instances by a free-text `name` and stored a
            # formula string per tool. Both are gone: instances key on
            # user_id, and the maths lives in the capability.
            conn.execute("DROP TABLE IF EXISTS tools")
            conn.execute("DROP TABLE IF EXISTS user_tools")
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tools (
                tool_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                capability TEXT NOT NULL,
                inputs TEXT NOT NULL,
                arg_map TEXT NOT NULL,
                result_key TEXT NOT NULL,
                output_label TEXT NOT NULL,
                output_prefix TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tools (
                user_id TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                input_values TEXT NOT NULL,
                result TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, tool_id)
            )
            """
        )
        for tool_id, spec in _BUILTIN.items():
            conn.execute(
                """
                INSERT INTO tools (tool_id, name, capability, inputs, arg_map, result_key,
                                   output_label, output_prefix, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(tool_id) DO UPDATE SET
                    name = excluded.name, capability = excluded.capability,
                    inputs = excluded.inputs, arg_map = excluded.arg_map,
                    result_key = excluded.result_key, output_label = excluded.output_label,
                    output_prefix = excluded.output_prefix, active = 1
                """,
                (tool_id, spec["name"], spec["capability"], json.dumps(spec["inputs"]),
                 json.dumps(spec["arg_map"]), spec["result_key"], spec["output_label"],
                 spec.get("output_prefix")),
            )


def _decode(value: str | None, *, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _tool_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "tool_id": row["tool_id"],
        "name": row["name"],
        # The client's renderer branches on this to decide whether to evaluate
        # a formula locally or POST to /api/tools/execute. Always "server"
        # here: there is no `formula` field to evaluate, deliberately, because
        # a browser-side copy of the EMI formula is a second implementation
        # that can disagree with the sentence the agent already wrote.
        #
        # Omitting it is not neutral -- `undefined` matches neither branch, so
        # the calculator renders and then does nothing on submit.
        "execution": "server",
        "capability": row["capability"],
        "inputs": _decode(row["inputs"], default=[]),
        "arg_map": _decode(row["arg_map"], default={}),
        "result_key": row["result_key"],
        "output_label": row["output_label"],
        "output_prefix": row["output_prefix"],
        "active": bool(row["active"]),
    }


def get_tools() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tools WHERE active = 1 ORDER BY name").fetchall()
    return [_tool_payload(row) for row in rows]


def get_tool_by_id(tool_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tools WHERE tool_id = ? AND active = 1",
                           (tool_id,)).fetchone()
    return _tool_payload(row) if row is not None else None


def run_tool(tool_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Compute a calculator's result through its capability.

    Raises KeyError for an unknown tool and ValueError for unusable inputs, so
    the route can map them to 404 and 400 without inspecting strings.
    """
    tool = get_tool_by_id(tool_id)
    if tool is None:
        raise KeyError(tool_id)

    kwargs: dict[str, Any] = {}
    for widget_key, capability_arg in tool["arg_map"].items():
        raw = inputs.get(widget_key)
        if raw is None or raw == "":
            raise ValueError(f"Missing input: {widget_key}")
        try:
            kwargs[capability_arg] = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"'{widget_key}' must be a number") from None

    # Capabilities register themselves as a side effect of importing
    # capabilities_impl. Doing that here rather than relying on someone else
    # having done it makes this callable from a test, a script or a route
    # without an ordering rule nobody would remember.
    import capabilities_impl  # noqa: F401
    from agent_platform.capabilities import DEFAULT_REGISTRY

    if not DEFAULT_REGISTRY.has(tool["capability"]):
        raise KeyError(f"capability {tool['capability']} is not registered")
    try:
        result = DEFAULT_REGISTRY.invoke(tool["capability"], **kwargs)
    except Exception as exc:                    # noqa: BLE001 - surfaced as 400
        raise ValueError(str(exc)) from exc

    value = result.get(tool["result_key"]) if isinstance(result, dict) else None
    return {
        "tool_id": tool_id,
        # `result` is the headline number, per the client's spec:
        #   POST /api/tools/execute -> { "result": ..., "output_label": "..." }
        # It used to be the whole capability dict, which a client following
        # that spec renders as "[object Object]".
        "result": value,
        # The same number under the name our own Playground reads, and the
        # full capability output for anything that wants the breakdown --
        # total interest, total invested. Additive; neither is in the spec.
        "value": value,
        "breakdown": result if isinstance(result, dict) else {},
        "output_label": tool["output_label"],
        "output_prefix": tool["output_prefix"],
    }


def save_user_tool(*, user_id: str, tool_id: str, input_values: dict[str, Any],
                   result: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_tools (user_id, tool_id, input_values, result, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, tool_id) DO UPDATE SET
                input_values = excluded.input_values,
                result = excluded.result,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, tool_id, json.dumps(input_values),
             json.dumps(result) if result is not None else None),
        )
        row = conn.execute("SELECT * FROM user_tools WHERE user_id = ? AND tool_id = ?",
                           (user_id, tool_id)).fetchone()
    return {
        "user_id": row["user_id"],
        "tool_id": row["tool_id"],
        "input_values": _decode(row["input_values"], default={}),
        "result": _decode(row["result"], default={}),
        "updated_at": row["updated_at"],
    }


def get_saved_tools(*, user_id: str) -> list[dict[str, Any]]:
    """A user's saved calculator instances, newest first.

    Joined against the live tool definition rather than a copy taken at save
    time, so a calculator whose inputs changed shape does not resurrect the
    old form.
    """
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT u.*, t.name AS tool_name, t.capability, t.inputs, t.arg_map,
                   t.result_key, t.output_label, t.output_prefix
            FROM user_tools u JOIN tools t ON t.tool_id = u.tool_id
            WHERE u.user_id = ? AND t.active = 1
            ORDER BY u.updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "user_id": row["user_id"],
            "tool_id": row["tool_id"],
            "input_values": _decode(row["input_values"], default={}),
            "result": _decode(row["result"], default={}),
            "updated_at": row["updated_at"],
            "tool": {
                "tool_id": row["tool_id"],
                "name": row["tool_name"],
                # The saved-tools tab re-renders through the same generic
                # renderer as the inline card (spec §6), so it branches on
                # `execution` here too. Omitting it drew a form that did
                # nothing on submit.
                "execution": "server",
                "capability": row["capability"],
                "inputs": _decode(row["inputs"], default=[]),
                "arg_map": _decode(row["arg_map"], default={}),
                "result_key": row["result_key"],
                "output_label": row["output_label"],
                "output_prefix": row["output_prefix"],
            },
        }
        for row in rows
    ]


if __name__ == "__main__":
    init_db()
    print(json.dumps(get_tools(), indent=2))

"""HTTP surface for the inline calculators.

Deliberately thin: every calculation goes through tool_store.run_tool, which
runs the same registered capability the agent itself calls. There is no
formula evaluation here and none in the client — see tool_store's docstring
for why a second implementation of the same arithmetic is the thing to avoid.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import tool_store

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ExecutePayload(BaseModel):
    tool_id: str
    inputs: dict[str, Any] = {}


class SavePayload(BaseModel):
    tool_id: str
    # One identity namespace under two names. The app keys everything on a
    # person's `name` -- its spec says saving persists "against the user's
    # name" -- while our own routes use user_id. Accepting only one of them
    # meant the client's own saveTool(name, ...) would 422 the moment it was
    # wired up, which its author had already written and left unwired.
    user_id: str | None = None
    name: str | None = None
    input_values: dict[str, Any] = {}
    result: dict[str, Any] | None = None

    @property
    def identity(self) -> str:
        return (self.user_id or self.name or "").strip()


@router.get("")
def list_tools() -> list[dict[str, Any]]:
    """Every calculator, with its input fields — enough for a client to render
    one without knowing anything about it in advance."""
    return tool_store.get_tools()


@router.post("/execute")
def execute_tool(payload: ExecutePayload) -> dict[str, Any]:
    """Compute a result. Same capability, same numbers as the chat answer."""
    try:
        return tool_store.run_tool(payload.tool_id, payload.inputs)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown tool_id '{payload.tool_id}'")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/save")
def save_tool(payload: SavePayload) -> dict[str, Any]:
    """Remember a user's inputs for a calculator, so it comes back filled in."""
    if not payload.identity:
        raise HTTPException(status_code=400, detail="Pass user_id or name")
    if tool_store.get_tool_by_id(payload.tool_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool_id '{payload.tool_id}'")
    return tool_store.save_user_tool(
        user_id=payload.identity, tool_id=payload.tool_id,
        input_values=payload.input_values, result=payload.result,
    )


@router.get("/saved")
def saved_tools(user_id: str | None = None, name: str | None = None) -> list[dict[str, Any]]:
    """A user's saved calculators. Same identity the chat and history use —
    `name` and `user_id` are two spellings of one namespace."""
    identity = (user_id or name or "").strip()
    if not identity:
        raise HTTPException(status_code=400, detail="Pass ?user_id= or ?name=")
    return tool_store.get_saved_tools(user_id=identity)

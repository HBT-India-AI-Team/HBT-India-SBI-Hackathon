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
    user_id: str
    tool_id: str
    input_values: dict[str, Any] = {}
    result: dict[str, Any] | None = None


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
    if tool_store.get_tool_by_id(payload.tool_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool_id '{payload.tool_id}'")
    return tool_store.save_user_tool(
        user_id=payload.user_id, tool_id=payload.tool_id,
        input_values=payload.input_values, result=payload.result,
    )


@router.get("/saved")
def saved_tools(user_id: str) -> list[dict[str, Any]]:
    """A user's saved calculators. Same `user_id` the chat session uses."""
    return tool_store.get_saved_tools(user_id=user_id)

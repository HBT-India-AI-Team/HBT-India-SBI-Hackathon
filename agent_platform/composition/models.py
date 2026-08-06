"""Config-driven building blocks for a reusable agent.

These models are intentionally generic: nothing here knows about "leads" or
"banking". An agent is fully described by an AgentDefinition (agent.yaml)
plus one or more Skills (skills_library/<skill_id>/). Adding a new agent
means adding new YAML/Markdown, not new Python classes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agent_platform.runtime.context import RunContext


class CapabilityRef(BaseModel):
    """A capability an agent is allowed to use, resolved by name against the
    tool registry at gather time. `kind` documents where it will eventually
    come from (local tool today, MCP/A2A later) without changing the shape
    agents declare against.
    """

    name: str
    kind: str = "tool"  # "tool" | "mcp" (future) | "a2a" (future)


class GovernanceConfig(BaseModel):
    permissions: list[str] = Field(default_factory=list)
    data_scope: list[str] = Field(default_factory=list)
    hitl_conditions: list[str] = Field(default_factory=list)
    confidence_threshold: float = 0.7
    max_llm_retries: int = 1


class LLMConfig(BaseModel):
    # Must match a model actually present on OLLAMA_HOST (see backend/.env) — granite4.1:3b was
    # never pulled there, only on individual developers' local Ollama installs, so any agent that
    # inherited this default without an explicit override would fail at request time in real use.
    # gemma4:12b is what every hand-built demo agent (lead_discovery, lead_qualification, proposal)
    # already uses.
    model: str = "gemma4:12b"
    temperature: float = 0.0
    seed: int = 7
    timeout_seconds: int = 120


class AgentDefinition(BaseModel):
    """The contents of agents/<agent_id>/agent.yaml."""

    agent_id: str
    version: str
    purpose: str
    skills: list[str] = Field(default_factory=list)
    pipeline: list[str]
    capabilities: list[CapabilityRef] = Field(default_factory=list)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    routable: bool = True
    draft: bool = False


class Skill(BaseModel):
    """The contents of skills_library/<skill_id>/skill.yaml plus the files it
    points to, already loaded into memory. `rules`/`output_contract` are
    optional — a skill either bundles deterministic gates/factors/composite/
    product_fit logic alongside its instructions, or is guidance-only. That's
    a property of one skill's content, not a different kind of skill: the
    loader (agent_platform/stages/skill_selection.py's load_skills stage)
    treats every skill identically, whether or not it has rules.
    """

    skill_id: str
    version: str
    description: str
    instructions_text: str
    shared_text: str = ""
    task_prompt_text: str = ""
    output_contract: dict[str, Any] | None = None
    rules: dict[str, Any] | None = None

    @property
    def has_rules(self) -> bool:
        return self.rules is not None


class AgentBundle(BaseModel):
    """What the AgentLoader hands back: a fully resolved, callable agent."""

    definition: AgentDefinition
    skills: dict[str, Skill] = Field(default_factory=dict)

    @property
    def agent_id(self) -> str:
        return self.definition.agent_id

    def active_skills(self, ctx: "RunContext") -> list[Skill]:
        """The Skills this run should use: whichever `load_skills` resolved
        onto ctx.active_skill_ids, or every declared skill if no loading has
        happened yet (before load_skills runs in the pipeline). Unknown ids
        are silently skipped rather than raising, since ctx.active_skill_ids
        may include a caller-supplied override that doesn't match anything.
        """
        if ctx.active_skill_ids:
            return [self.skills[sid] for sid in ctx.active_skill_ids if sid in self.skills]
        return list(self.skills.values())

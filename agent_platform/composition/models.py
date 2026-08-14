"""Config-driven building blocks for a reusable agent.

These models are intentionally generic: nothing here knows about "leads" or
"banking". An agent is fully described by an AgentDefinition (agent.yaml)
plus one or more Skills (skills_library/<skill_id>/). Adding a new agent
means adding new YAML/Markdown, not new Python classes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

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
    # Must name a model actually present on OLLAMA_HOST (see backend/.env).
    # Nothing validates this, so a wrong value fails at request time as a 404
    # per call, which surfaces as empty replies rather than as an error --
    # granite4.1:3b was set as this default while never being pulled on the
    # host, and it read as the prompting having broken. Check /api/tags.
    model: str = "gemma4:12b"
    temperature: float = 0.0
    seed: int = 7
    timeout_seconds: int = 120
    # Ollama's thinking switch, sent only when set. None means "don't send it",
    # so every agent keeps its model's default behaviour unless it opts in.
    # Worth setting to False for a reasoning model behind a long system prompt:
    # qwen3.6:35b under FinGuru's 18k-character instructions produced 3,457
    # characters of `thinking` and then 81 characters of actual answer before
    # stopping. Not portable across models -- test the agent's own model first.
    think: bool | None = None


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
    # Which interface the Playground/embed should show by default — purely a
    # UI hint, nothing in the runtime reads it. "chat": free text, fields
    # extracted as needed (most agents). "form": a field-by-field form from
    # input_schema (agents with generic/opaque evidence shapes). "json": raw
    # payload editor (custom-pipeline agents like proposal/lead_discovery,
    # whose input doesn't reduce to a flat field list). "trigger": no input
    # at all — a lookup/check agent where clicking Run is the entire input.
    # "file": a document upload (e.g. fin_health's Excel report) parsed server-side into
    # evidence before the pipeline runs — see backend/excel_ingest.py and the admin
    # /test-run-file endpoint.
    input_mode: Literal["chat", "form", "json", "trigger", "file"] = "chat"
    # Optional canned input for the admin Playground's "Fill sample data" button — purely a
    # demo/UX convenience, never read by the runtime. Shape matches whatever this agent's own
    # input_mode expects: {"evidence": {...}} for form/json, {"message": "..."} for chat.
    demo_sample_input: dict[str, Any] | None = None


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
    # Which backend/archetypes/*.py archetype produced this skill, if it was
    # AI-generated — None for hand-written skills that predate the
    # archetype system (lead_discovery, proposal, etc.) or that aren't
    # managed by the "describe it"/"Fix with AI" generator at all.
    archetype: str | None = None

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

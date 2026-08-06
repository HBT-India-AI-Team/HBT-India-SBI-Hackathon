"""Admin API for the skill/agent editor UI (frontend/).

Lets someone create a new agent + skill scaffold, edit an existing agent's
config and skill files as text, validate before saving, and test-run an
agent — all without touching the filesystem directly. Deliberately reuses
the same models/loader/executor the CLI and main API use, so "valid" here
means exactly the same thing it means everywhere else in the platform:
AgentDefinition/Skill must load cleanly via load_agent().

This does NOT let someone write new stage code (a new kind of data lookup
or algorithm) — that's still a developer task. It covers agents built from
existing generic stages (load_input, reason_llm, validate_output, explain)
plus a new skill package (rules + instructions) — which is most of what a
new banking agent like the ones already built actually needs.
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.composition import evict, list_agents, load_agent
from agent_platform.composition.loader import AGENTS_DIR, SKILLS_DIR
from agent_platform.composition.models import AgentDefinition
from agent_platform.explainability import decision_record
from agent_platform.runtime.executor import invoke_agent
from agent_platform.runtime.pipeline import STAGE_REGISTRY

from . import agent_builder, agent_templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _agent_yaml_path(agent_id: str) -> Path:
    return AGENTS_DIR / agent_id / "agent.yaml"


def _skill_dir(skill_id: str) -> Path:
    return SKILLS_DIR / skill_id


def _read_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_skill_files(skill_id: str) -> dict:
    """rules/output_contract/task_prompt are only present if the manifest
    declares them — a skill either bundles deterministic logic or is
    guidance-only, and this reads whichever it actually is rather than
    assuming one shape.
    """
    skill_dir = _skill_dir(skill_id)
    skill_yaml_text = _read_if_exists(skill_dir / "skill.yaml")
    manifest = yaml.safe_load(skill_yaml_text) or {} if skill_yaml_text else {}
    rules = {
        rule_name: _read_if_exists(skill_dir / rel_path)
        for rule_name, rel_path in (manifest.get("rules") or {}).items()
    }
    return {
        "skill_yaml": skill_yaml_text,
        "instructions_md": _read_if_exists(skill_dir / manifest.get("instructions", "instructions.md")),
        "task_prompt_md": _read_if_exists(skill_dir / manifest["task_prompt"]) if manifest.get("task_prompt") else "",
        "output_contract_json": (
            _read_if_exists(skill_dir / manifest["output_contract"]) if manifest.get("output_contract") else ""
        ),
        "rules": rules,
    }


def _write_skill_files(skill_id: str, manifest: dict, files: "SkillFiles") -> None:
    skill_dir = _skill_dir(skill_id)
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(files.skill_yaml, encoding="utf-8")
    (skill_dir / manifest.get("instructions", "instructions.md")).write_text(files.instructions_md, encoding="utf-8")
    if manifest.get("task_prompt"):
        (skill_dir / manifest["task_prompt"]).write_text(files.task_prompt_md, encoding="utf-8")
    if manifest.get("output_contract"):
        (skill_dir / manifest["output_contract"]).write_text(files.output_contract_json, encoding="utf-8")
    for rule_name, rel_path in (manifest.get("rules") or {}).items():
        rule_path = skill_dir / rel_path
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(files.rules.get(rule_name, ""), encoding="utf-8")


def _rewrite_agent_skill_list(agent_id: str, list_key: str, *, add: str | None = None,
                               remove: str | None = None) -> None:
    path = _agent_yaml_path(agent_id)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    current = list(manifest.get(list_key) or [])
    if add is not None and add not in current:
        current.append(add)
    if remove is not None and remove in current:
        current.remove(remove)
    manifest[list_key] = current
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


@router.get("/stages")
def get_stages() -> dict:
    """Every stage name a pipeline can reference today. Building an agent
    that only needs these (plus new rules/YAML) requires no new code.
    """
    return {"stages": sorted(STAGE_REGISTRY.keys())}


@router.get("/capabilities")
def get_capabilities() -> dict:
    return {"capabilities": DEFAULT_REGISTRY.list_tools()}


@router.get("/templates")
def get_templates() -> dict:
    """Starter shapes the New Agent flow can scaffold from."""
    return {"templates": agent_templates.list_templates()}


@router.get("/skills")
def list_skills() -> dict:
    """Every skills_library/<id> directory, kind inferred structurally
    (presence of `output_contract` in its manifest, same as the loader
    itself distinguishes deterministic from procedural — the informational
    `kind:` field in skill.yaml is never read by the loader, so this
    doesn't trust it as the source of truth either). Feeds the "attach
    existing skill" pickers in the Add Skill / Add Dynamic Skill modals.
    """
    skills = []
    for skill_yaml_path in sorted(SKILLS_DIR.glob("*/skill.yaml")):
        skill_id = skill_yaml_path.parent.name
        manifest = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8")) or {}
        kind = "deterministic" if "output_contract" in manifest else "procedural"
        skills.append({"skill_id": skill_id, "kind": kind, "description": manifest.get("description", "")})
    return {"skills": skills}


@router.get("/agents")
def get_agents_summary() -> dict:
    agents = []
    for agent_id in list_agents():
        try:
            bundle = load_agent(agent_id)
            agents.append({
                "agent_id": agent_id,
                "version": bundle.definition.version,
                "purpose": bundle.definition.purpose,
                "skills": bundle.definition.skills,
                "pipeline": bundle.definition.pipeline,
                "routable": bundle.definition.routable,
                "draft": bundle.definition.draft,
                "input_schema": bundle.definition.input_schema,
            })
        except Exception as exc:  # noqa: BLE001 - surface a broken agent, don't hide it
            agents.append({"agent_id": agent_id, "error": str(exc)})
    return {"agents": agents}


@router.get("/agents/{agent_id}/files")
def get_agent_files(agent_id: str) -> dict:
    agent_yaml_path = _agent_yaml_path(agent_id)
    if not agent_yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    agent_yaml_text = agent_yaml_path.read_text(encoding="utf-8")
    agent_manifest = yaml.safe_load(agent_yaml_text) or {}
    skill_ids = agent_manifest.get("skills") or []

    return {
        "agent_id": agent_id,
        "agent_yaml": agent_yaml_text,
        "skills": {sid: _read_skill_files(sid) for sid in skill_ids},
    }


class SkillFiles(BaseModel):
    skill_yaml: str
    instructions_md: str = ""
    task_prompt_md: str = ""
    output_contract_json: str = ""
    rules: dict[str, str] = {}


class AgentFilesPayload(BaseModel):
    agent_yaml: str
    skills: dict[str, SkillFiles]


@router.put("/agents/{agent_id}/files")
def save_agent_files(agent_id: str, payload: AgentFilesPayload) -> dict:
    # -- validate everything before writing anything -----------------------
    try:
        agent_manifest = yaml.safe_load(payload.agent_yaml) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"agent.yaml is not valid YAML: {exc}")

    try:
        definition = AgentDefinition.model_validate(agent_manifest)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"agent.yaml failed validation: {exc}")

    if definition.agent_id != agent_id:
        raise HTTPException(
            status_code=400,
            detail=f"agent.yaml declares agent_id='{definition.agent_id}', expected '{agent_id}'",
        )

    unknown_stages = [s for s in definition.pipeline if s not in STAGE_REGISTRY]
    if unknown_stages:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline stage(s): {unknown_stages}")

    required_skill_ids = set(definition.skills)
    payload_skill_ids = set(payload.skills.keys())
    if payload_skill_ids != required_skill_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"payload.skills keys {sorted(payload_skill_ids)} don't match agent.yaml's "
                f"skills {sorted(required_skill_ids)} (missing: "
                f"{sorted(required_skill_ids - payload_skill_ids)}, unexpected: "
                f"{sorted(payload_skill_ids - required_skill_ids)}) — use the Add/Remove Skill "
                f"actions, or reload the page, rather than hand-editing this list"
            ),
        )

    skill_manifests: dict[str, dict] = {}
    for skill_id, files in payload.skills.items():
        try:
            skill_manifest = yaml.safe_load(files.skill_yaml) or {}
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail=f"skills/{skill_id}/skill.yaml is not valid YAML: {exc}")

        missing = {"skill_id", "version", "instructions"} - skill_manifest.keys()
        if missing:
            raise HTTPException(
                status_code=400, detail=f"skills/{skill_id}/skill.yaml missing required key(s): {sorted(missing)}"
            )
        if skill_manifest.get("skill_id") != skill_id:
            raise HTTPException(
                status_code=400,
                detail=f"skills/{skill_id}/skill.yaml declares skill_id="
                       f"'{skill_manifest.get('skill_id')}', expected '{skill_id}'",
            )

        if skill_manifest.get("output_contract") and files.output_contract_json.strip():
            try:
                json.loads(files.output_contract_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=400, detail=f"skills/{skill_id}/output_contract.json is not valid JSON: {exc}"
                )

        for rule_name, rule_text in files.rules.items():
            try:
                yaml.safe_load(rule_text)
            except yaml.YAMLError as exc:
                raise HTTPException(
                    status_code=400, detail=f"skills/{skill_id}/rules/{rule_name} is not valid YAML: {exc}"
                )

        skill_manifests[skill_id] = skill_manifest

    # -- write -----------------------
    agent_dir = AGENTS_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(payload.agent_yaml, encoding="utf-8")

    for skill_id, files in payload.skills.items():
        _write_skill_files(skill_id, skill_manifests[skill_id], files)

    # -- full end-to-end validation, same path invoke_agent() uses -----------------------
    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        return {"status": "saved_with_errors", "error": str(exc)}

    return {"status": "ok"}


class NewAgentPayload(BaseModel):
    agent_id: str
    skill_id: str | None = None
    purpose: str = ""
    template_id: str = "blank"


@router.post("/agents")
def create_agent(payload: NewAgentPayload) -> dict:
    agent_id = payload.agent_id.strip()
    if not agent_id or not agent_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="agent_id must be alphanumeric/underscore only")
    if _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=400, detail=f"agent '{agent_id}' already exists")

    try:
        template = agent_templates.get_template(payload.template_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    skill_id = (payload.skill_id or agent_id).strip()
    purpose = payload.purpose.strip() or "Describe what this agent does."

    agent_yaml = template.render_agent_yaml(agent_id, skill_id, purpose)
    (AGENTS_DIR / agent_id).mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(agent_yaml, encoding="utf-8")

    skill_dir = _skill_dir(skill_id)
    if not skill_dir.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "rules").mkdir(exist_ok=True)
        for rel_path, content in template.render_skill_files(skill_id, purpose).items():
            file_path = skill_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

    return {"status": "created", "agent_id": agent_id, "skill_id": skill_id, "template_id": template.id}


class GenerateAgentPayload(BaseModel):
    agent_id: str
    purpose: str


@router.post("/agents/generate")
def generate_agent(payload: GenerateAgentPayload) -> dict:
    """Describe-it flow: an LLM authors real gates/factors/thresholds/
    products from a plain-language description (agent_builder.generate_spec
    never writes anything invalid — see that module's docstring), and the
    result always lands as draft: true, routable: false for a human to
    review before it's trusted, exactly like create_agent but LLM-authored
    instead of template-placeholder content.
    """
    agent_id = payload.agent_id.strip()
    if not agent_id or not agent_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="agent_id must be alphanumeric/underscore only")
    if _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=400, detail=f"agent '{agent_id}' already exists")

    purpose = payload.purpose.strip()
    if not purpose:
        raise HTTPException(status_code=400, detail="purpose (the description) must not be empty")

    generation = agent_builder.generate_agent_skills(purpose=purpose, agent_id=agent_id)
    skill_id = generation.skills[0].skill_id  # primary — matches agent_id for the (common) single-skill case

    # Top-level agent.yaml purpose/evidence should describe the whole request, not just the
    # primary skill's own scoped spec — matters once a description splits into multiple skills.
    seen_paths: set[str] = set()
    combined_evidence_fields = []
    for skill in generation.skills:
        for field in skill.result.spec.get("evidence_fields", []):
            if field["path"] not in seen_paths:
                seen_paths.add(field["path"])
                combined_evidence_fields.append(field)
    top_level_spec = {"purpose": purpose, "evidence_fields": combined_evidence_fields}

    agent_yaml = agent_builder.render_agent_yaml(
        agent_id, [s.skill_id for s in generation.skills], top_level_spec,
    )
    (AGENTS_DIR / agent_id).mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(agent_yaml, encoding="utf-8")

    for skill in generation.skills:
        skill_dir = _skill_dir(skill.skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "rules").mkdir(exist_ok=True)
        for rel_path, content in agent_builder.render_skill_files(skill.skill_id, skill.result.spec).items():
            file_path = skill_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

    skills_summary = [
        {"skill_id": s.skill_id, "description": s.description, "used_fallback": s.result.used_fallback}
        for s in generation.skills
    ]
    used_fallback = any(s.result.used_fallback for s in generation.skills)
    attempts = sum(s.result.attempts for s in generation.skills)

    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        return {
            "status": "saved_with_errors", "agent_id": agent_id, "skill_id": skill_id, "error": str(exc),
            "used_fallback": used_fallback, "attempts": attempts, "skills": skills_summary,
        }

    return {
        "status": "ok", "agent_id": agent_id, "skill_id": skill_id,
        "used_fallback": used_fallback, "attempts": attempts, "skills": skills_summary,
    }


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _generate_agent_events(agent_id: str, purpose: str):
    """Same work as generate_agent, but reported step-by-step over SSE as it
    actually happens (decompose → each skill's LLM call → save → validate),
    instead of leaving the client to guess at timing for a call that can
    take well over a minute. generate_agent_skills does the real work on a
    background thread and reports through on_progress; this generator just
    relays that queue out over the wire, then does the same file-write/
    validate steps generate_agent already does, each bracketed by its own
    start/done event.
    """
    events: queue.Queue = queue.Queue()
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["generation"] = agent_builder.generate_agent_skills(
                purpose=purpose, agent_id=agent_id, on_progress=events.put,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
            outcome["error"] = exc
        finally:
            events.put(None)  # sentinel: worker is done

    threading.Thread(target=worker, daemon=True).start()

    while (event := events.get()) is not None:
        yield _sse(event)

    if "error" in outcome:
        yield _sse({"step": "final", "result": {
            "status": "error", "agent_id": agent_id, "error": str(outcome["error"]),
        }})
        return

    generation: agent_builder.MultiSkillGeneration = outcome["generation"]
    skill_id = generation.skills[0].skill_id

    yield _sse({"step": "save", "status": "start"})
    seen_paths: set[str] = set()
    combined_evidence_fields = []
    for skill in generation.skills:
        for field in skill.result.spec.get("evidence_fields", []):
            if field["path"] not in seen_paths:
                seen_paths.add(field["path"])
                combined_evidence_fields.append(field)
    top_level_spec = {"purpose": purpose, "evidence_fields": combined_evidence_fields}

    agent_yaml = agent_builder.render_agent_yaml(
        agent_id, [s.skill_id for s in generation.skills], top_level_spec,
    )
    (AGENTS_DIR / agent_id).mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(agent_yaml, encoding="utf-8")

    for skill in generation.skills:
        skill_dir = _skill_dir(skill.skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "rules").mkdir(exist_ok=True)
        for rel_path, content in agent_builder.render_skill_files(skill.skill_id, skill.result.spec).items():
            file_path = skill_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
    yield _sse({"step": "save", "status": "done"})

    skills_summary = [
        {"skill_id": s.skill_id, "description": s.description, "used_fallback": s.result.used_fallback}
        for s in generation.skills
    ]
    used_fallback = any(s.result.used_fallback for s in generation.skills)
    attempts = sum(s.result.attempts for s in generation.skills)

    yield _sse({"step": "validate", "status": "start"})
    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        yield _sse({"step": "validate", "status": "error", "error": str(exc)})
        yield _sse({"step": "final", "result": {
            "status": "saved_with_errors", "agent_id": agent_id, "skill_id": skill_id, "error": str(exc),
            "used_fallback": used_fallback, "attempts": attempts, "skills": skills_summary,
        }})
        return

    yield _sse({"step": "validate", "status": "done"})
    yield _sse({"step": "final", "result": {
        "status": "ok", "agent_id": agent_id, "skill_id": skill_id,
        "used_fallback": used_fallback, "attempts": attempts, "skills": skills_summary,
    }})


@router.post("/agents/generate/stream")
def generate_agent_stream(payload: GenerateAgentPayload) -> StreamingResponse:
    """Same as POST /agents/generate, but streamed over SSE so the client can
    show real per-step progress instead of a single opaque spinner for a
    call that can take well over a minute.
    """
    agent_id = payload.agent_id.strip()
    if not agent_id or not agent_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="agent_id must be alphanumeric/underscore only")
    if _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=400, detail=f"agent '{agent_id}' already exists")

    purpose = payload.purpose.strip()
    if not purpose:
        raise HTTPException(status_code=400, detail="purpose (the description) must not be empty")

    return StreamingResponse(
        _generate_agent_events(agent_id, purpose),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class RefineAgentPayload(BaseModel):
    feedback: str


@router.post("/agents/{agent_id}/refine")
def refine_agent(agent_id: str, payload: RefineAgentPayload) -> dict:
    """Human-in-the-loop correction for a draft agent: describe what's
    wrong in plain language, the LLM corrects the existing rules (given as
    context, not regenerated from scratch). Only for draft agents — a live
    agent's rules get edited directly via the Files tab, not through this.
    """
    try:
        bundle = load_agent(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    if not bundle.definition.draft:
        raise HTTPException(status_code=400, detail="refine is only for draft agents — edit files directly for a live agent")

    feedback = payload.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="feedback must not be empty")

    skill_id = bundle.definition.skills[0]
    current_rules = _read_skill_files(skill_id)["rules"]
    result = agent_builder.refine_spec(current_rules=current_rules, feedback=feedback)
    if not result.ok:
        raise HTTPException(status_code=400, detail="Could not produce a valid correction: " + "; ".join(result.errors))

    skill_dir = _skill_dir(skill_id)
    for rel_path, content in agent_builder.render_skill_files(skill_id, result.spec).items():
        file_path = skill_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        return {"status": "saved_with_errors", "agent_id": agent_id, "error": str(exc)}

    return {"status": "ok", "agent_id": agent_id}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str) -> dict:
    """Removes agents/<agent_id>/ only — its skill package is left
    untouched, since skills can be intentionally shared across agents.
    """
    agent_dir = AGENTS_DIR / agent_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    shutil.rmtree(agent_dir)
    evict(agent_id)
    return {"status": "deleted", "agent_id": agent_id}


def _reload_after_skill_change(agent_id: str, skill_id: str) -> dict:
    evict(agent_id)
    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; agent.yaml is already rewritten
        return {"status": "saved_with_errors", "agent_id": agent_id, "skill_id": skill_id, "error": str(exc)}
    return {"status": "ok", "agent_id": agent_id, "skill_id": skill_id}


def _referenced_skill_ids(manifest: dict) -> set[str]:
    return set(manifest.get("skills") or [])


class AddSkillPayload(BaseModel):
    skill_id: str
    mode: Literal["scaffold", "attach_existing"] = "scaffold"
    has_rules: bool = True
    template_id: str = "blank"
    description: str = ""
    purpose: str = ""


@router.post("/agents/{agent_id}/skills")
def add_skill(agent_id: str, payload: AddSkillPayload) -> dict:
    """Adds a skill to agent.yaml's `skills:` list — load_skills can load it
    alongside any of the agent's other skills. `has_rules` (scaffold mode
    only) decides whether the new skill package gets real gates/factors/
    composite/product_fit or is guidance-only; either way it's the same
    kind of skill, just with or without rules.
    """
    agent_yaml_path = _agent_yaml_path(agent_id)
    if not agent_yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    skill_id = payload.skill_id.strip()
    if not skill_id or not skill_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="skill_id must be alphanumeric/underscore only")

    manifest = yaml.safe_load(agent_yaml_path.read_text(encoding="utf-8")) or {}
    if skill_id in _referenced_skill_ids(manifest):
        raise HTTPException(status_code=400, detail=f"'{skill_id}' is already referenced by this agent")

    skill_dir = _skill_dir(skill_id)
    if payload.mode == "scaffold":
        if skill_dir.exists():
            raise HTTPException(
                status_code=400,
                detail=f"skill '{skill_id}' already exists in skills_library — use mode=attach_existing",
            )
        if payload.has_rules:
            try:
                template = agent_templates.get_template(payload.template_id)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "rules").mkdir(exist_ok=True)
            for rel_path, content in template.render_skill_files(
                skill_id, payload.purpose or payload.description
            ).items():
                file_path = skill_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
        else:
            skill_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in agent_templates.render_procedural_skill_files(
                skill_id, payload.description
            ).items():
                (skill_dir / rel_path).write_text(content, encoding="utf-8")
    else:
        skill_yaml_path = skill_dir / "skill.yaml"
        if not skill_yaml_path.exists():
            raise HTTPException(status_code=404, detail=f"No such skill '{skill_id}' in skills_library")
        # attach_existing: any existing skill can be attached regardless of whether it happens
        # to have rules — there's only one kind of skill now, not a deterministic/procedural split.

    _rewrite_agent_skill_list(agent_id, "skills", add=skill_id)
    return _reload_after_skill_change(agent_id, skill_id)


@router.delete("/agents/{agent_id}/skills/{skill_id}")
def remove_skill(agent_id: str, skill_id: str) -> dict:
    """Detaches skill_id from agent.yaml's `skills:` list. Never deletes
    skills_library/<skill_id>/ — skill packages are shared and intentionally
    left on disk, same as delete_agent already does for a whole agent. Any
    skill can be removed, including down to zero — there's no "primary"
    skill that's protected from removal.
    """
    agent_yaml_path = _agent_yaml_path(agent_id)
    if not agent_yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    manifest = yaml.safe_load(agent_yaml_path.read_text(encoding="utf-8")) or {}
    if skill_id not in (manifest.get("skills") or []):
        raise HTTPException(status_code=404, detail=f"'{skill_id}' is not a skill on agent '{agent_id}'")

    _rewrite_agent_skill_list(agent_id, "skills", remove=skill_id)
    return _reload_after_skill_change(agent_id, skill_id)


class TestRunPayload(BaseModel):
    input: dict[str, Any] = {}


@router.post("/agents/{agent_id}/test-run")
def test_run_agent(agent_id: str, payload: TestRunPayload) -> dict:
    try:
        ctx = invoke_agent(agent_id, payload.input)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {
        "run_id": ctx.run_id,
        "decision": ctx.decision,
        "explanation": ctx.explanation,
        "error": ctx.error,
    }


class MarkdownPayload(BaseModel):
    explanation: dict[str, Any]


@router.post("/explain/markdown")
def render_explanation_markdown(payload: MarkdownPayload) -> dict:
    """Renders a decision_record.build()-shaped `explanation` (whatever a
    test-run/agent_router response already carries) into a single readable
    Markdown string via the existing render_markdown() — previously unused
    over HTTP. Stateless: takes the explanation the caller already has
    rather than looking up a stored run, so it works uniformly for both
    direct agent runs and agent_router results (agent_router forwards the
    chosen agent's own explanation, same shape either way).
    """
    try:
        markdown = decision_record.render_markdown(payload.explanation)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"explanation is missing required field: {exc}")
    return {"markdown": markdown}

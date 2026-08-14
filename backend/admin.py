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
import os
import queue
import re
import shutil
import threading
import time
from contextvars import copy_context
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.llm import speech_stream
from agent_platform.composition import evict, list_agents, load_agent
from agent_platform.composition.loader import AGENTS_DIR, SKILLS_DIR
from agent_platform.composition.models import AgentDefinition
from agent_platform.explainability import decision_record
from agent_platform.llm import OllamaAdapter, OllamaContentError, OllamaError
from agent_platform.llm.ollama_adapter import read_call, read_recent_calls
from agent_platform.runtime import chat
from agent_platform.runtime.executor import invoke_agent
from agent_platform.runtime.pipeline import STAGE_REGISTRY

from . import agent_builder, agent_templates, api_keys, excel_ingest
from .archetypes import get_archetype, list_archetypes

router = APIRouter(prefix="/admin", tags=["admin"])


_ACCESS_LINE = re.compile(r'"(?P<verb>[A-Z]+) (?P<path>[^ ?]+)[^"]*" (?P<status>\d{3})')

# Path segments that are identifiers, so /agents/finguru/chat and
# /agents/other/chat collapse into one row instead of flooding the table.
_ID_SEGMENT = re.compile(r"^(?:run_|chat_)[0-9a-f]+$|^[0-9a-f]{8,}$")


def _normalise(path: str) -> str:
    parts = [("{id}" if _ID_SEGMENT.match(seg) else seg) for seg in path.split("/")]
    return "/".join(parts)


@router.get("/api-surface")
def api_surface() -> dict:
    """Every endpoint this backend serves, plus what has actually been called.

    Exists because a client team was calling us with a field name we did not
    read and a flag nested a level below where we looked, and neither side
    could see it. The declared list answers "does this endpoint exist"; the
    traffic list answers "what are they really hitting" — a 404 against a path
    that is not in the declared list is a wrong URL, and that is the failure
    this is here to make visible.

    Traffic is parsed from uvicorn's own access log, so it only covers the
    current process. It resets when the backend restarts.
    """
    from backend.main import app

    declared = []
    for path, operations in app.openapi().get("paths", {}).items():
        if path.startswith(("/openapi", "/docs", "/redoc")):
            continue
        for verb, op in operations.items():
            if verb.upper() in {"HEAD", "OPTIONS"} or not isinstance(op, dict):
                continue
            text = op.get("description") or op.get("summary") or ""
            declared.append({
                "method": verb.upper(),
                "path": path,
                "audience": "admin" if path.startswith("/admin") else "client",
                "keyed": any(p.get("name") == "x-api-key"
                             for p in op.get("parameters", []) if isinstance(p, dict)),
                "summary": " ".join(text.split())[:160],
            })

    known = {(r["method"], r["path"]) for r in declared}
    seen: dict[tuple, dict] = {}
    log = Path(__file__).resolve().parent.parent / "uvicorn_out.log"
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _ACCESS_LINE.search(line)
            if not m:
                continue
            verb, path, status = m["verb"], _normalise(m["path"]), int(m["status"])
            row = seen.setdefault((verb, path), {"method": verb, "path": path,
                                                 "count": 0, "errors": 0, "last_status": status})
            row["count"] += 1
            row["last_status"] = status
            if status >= 400:
                row["errors"] += 1
    except OSError:
        pass

    # Declared paths are templates ("/agents/{agent_id}/chat"), so a real path
    # never matches by string equality. Compile each into a pattern instead.
    patterns = [
        (method, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(path)
                                         .replace(r"\{", "{").replace(r"\}", "}")) + "$"))
        for method, path in known
    ]

    traffic = sorted(seen.values(), key=lambda r: r["count"], reverse=True)
    for row in traffic:
        # An unrecognised path is the wrong-endpoint case this endpoint exists
        # to surface. Matched against the un-normalised path, since {id}
        # substitution would otherwise make a typo'd path look templated.
        row["recognised"] = row["path"].startswith(("/openapi", "/docs", "/redoc")) or any(
            row["method"] == method and pattern.match(row["path"].replace("{id}", "x"))
            for method, pattern in patterns
        )

    return {"declared": declared, "traffic": traffic,
            "log_note": "Traffic is from the current backend process only; it resets on restart."}


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


@router.get("/archetypes")
def get_archetypes() -> dict:
    """Agent shapes the "describe it" AI generator can produce — the picker
    shown before generation, so adding a new backend/archetypes/*.py module
    surfaces in the UI automatically without a frontend change.
    """
    return {"archetypes": list_archetypes()}


@router.get("/ollama-logs")
def get_ollama_logs(limit: int = 100) -> dict:
    """Every Ollama call attempt (including failed retries), most recent
    first — the Logs page's data source. Reads
    agent_platform.llm.ollama_adapter's logs/ollama_calls.jsonl.

    Summaries only. The request/response bodies come from the sibling route
    below when a row is expanded, so opening the page doesn't transfer every
    prompt and completion ever sent.
    """
    return {"calls": read_recent_calls(limit)}


@router.get("/ollama-logs/{offset}")
def get_ollama_log_detail(offset: int) -> dict:
    """The full record — request and response bodies included — behind one
    row of the list above, keyed by the `offset` that route handed out.
    """
    record = read_call(offset)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No logged call at offset {offset}")
    return record


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
                "input_mode": bundle.definition.input_mode,
                "demo_sample_input": bundle.definition.demo_sample_input,
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

    api_keys.get_or_create_key(agent_id)
    return {"status": "created", "agent_id": agent_id, "skill_id": skill_id, "template_id": template.id}


class GenerateAgentPayload(BaseModel):
    agent_id: str
    purpose: str
    archetype_id: str = "qualification"


def _combine_top_level_spec(purpose: str, archetype, skills: list) -> dict:
    """The top-level agent.yaml's declared-fields block should describe the
    whole request, not just one skill's own scoped spec — matters once a
    description splits into multiple skills. Which field to merge
    (evidence_fields, input_fields, ...) is archetype-specific.
    """
    seen_paths: set[str] = set()
    combined_fields = []
    for skill in skills:
        for field in skill.result.spec.get(archetype.merge_field, []):
            if field["path"] not in seen_paths:
                seen_paths.add(field["path"])
                combined_fields.append(field)
    top_level_spec = {"purpose": purpose, archetype.merge_field: combined_fields}

    # Not merged (unlike merge_field) — copied verbatim from the primary
    # skill's own spec, since these describe one skill's own output shape,
    # not something that makes sense combined across skills.
    primary_spec = skills[0].result.spec
    for key in archetype.primary_only_fields:
        top_level_spec[key] = primary_spec.get(key, [])

    return top_level_spec


@router.post("/agents/generate")
def generate_agent(payload: GenerateAgentPayload) -> dict:
    """Describe-it flow: an LLM authors a real spec (gates/factors/
    thresholds/products, or input/output fields + guidance, depending on
    archetype_id — agent_builder.generate_spec never writes anything
    invalid, see that module's docstring), and the result always lands as
    draft: true, routable: false for a human to review before it's trusted,
    exactly like create_agent but LLM-authored instead of
    template-placeholder content.
    """
    agent_id = payload.agent_id.strip()
    if not agent_id or not agent_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="agent_id must be alphanumeric/underscore only")
    if _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=400, detail=f"agent '{agent_id}' already exists")

    purpose = payload.purpose.strip()
    if not purpose:
        raise HTTPException(status_code=400, detail="purpose (the description) must not be empty")

    try:
        archetype = get_archetype(payload.archetype_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    generation = agent_builder.generate_agent_skills(purpose=purpose, agent_id=agent_id, archetype_id=archetype.id)
    skill_id = generation.skills[0].skill_id  # primary — matches agent_id for the (common) single-skill case

    top_level_spec = _combine_top_level_spec(purpose, archetype, generation.skills)
    agent_yaml = archetype.render_agent_yaml(agent_id, [s.skill_id for s in generation.skills], top_level_spec)
    (AGENTS_DIR / agent_id).mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(agent_yaml, encoding="utf-8")

    for skill in generation.skills:
        skill_dir = _skill_dir(skill.skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, content in archetype.render_skill_files(skill.skill_id, skill.result.spec).items():
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

    api_keys.get_or_create_key(agent_id)
    return {
        "status": "ok", "agent_id": agent_id, "skill_id": skill_id,
        "used_fallback": used_fallback, "attempts": attempts, "skills": skills_summary,
    }


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _generate_agent_events(agent_id: str, purpose: str, archetype_id: str):
    """Same work as generate_agent, but reported step-by-step over SSE as it
    actually happens (decompose → each skill's LLM call → save → validate),
    instead of leaving the client to guess at timing for a call that can
    take well over a minute. generate_agent_skills does the real work on a
    background thread and reports through on_progress; this generator just
    relays that queue out over the wire, then does the same file-write/
    validate steps generate_agent already does, each bracketed by its own
    start/done event.
    """
    archetype = get_archetype(archetype_id)
    events: queue.Queue = queue.Queue()
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["generation"] = agent_builder.generate_agent_skills(
                purpose=purpose, agent_id=agent_id, archetype_id=archetype.id, on_progress=events.put,
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
    top_level_spec = _combine_top_level_spec(purpose, archetype, generation.skills)
    agent_yaml = archetype.render_agent_yaml(agent_id, [s.skill_id for s in generation.skills], top_level_spec)
    (AGENTS_DIR / agent_id).mkdir(parents=True, exist_ok=True)
    _agent_yaml_path(agent_id).write_text(agent_yaml, encoding="utf-8")

    for skill in generation.skills:
        skill_dir = _skill_dir(skill.skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, content in archetype.render_skill_files(skill.skill_id, skill.result.spec).items():
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

    api_keys.get_or_create_key(agent_id)
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

    try:
        archetype = get_archetype(payload.archetype_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return StreamingResponse(
        _generate_agent_events(agent_id, purpose, archetype.id),
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

    Applies the same feedback to every rule-bearing skill on the agent
    independently (one refine_spec call each, same pattern
    generate_agent_skills already uses for independent per-skill
    generation) — a multi-skill agent's "fix everything" shouldn't require
    repeating the same feedback once per skill. Guidance-only skills have no
    rules to correct and are skipped. Succeeds as long as at least one
    skill's correction is valid; skills that fail are reported, not silently
    dropped.
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

    # has_rules covers qualification-shaped skills generated before the archetype
    # tag existed (their skill.yaml has no archetype: line); archetype is not None
    # covers every archetype-tagged skill, rule-bearing or not (e.g. conversational).
    refinable_skill_ids = [
        sid for sid in bundle.definition.skills
        if bundle.skills[sid].has_rules or bundle.skills[sid].archetype is not None
    ]
    if not refinable_skill_ids:
        raise HTTPException(status_code=400, detail="This agent has no skills this generator can correct")

    skills_summary = []
    for skill_id in refinable_skill_ids:
        skill = bundle.skills[skill_id]
        try:
            archetype = get_archetype(skill.archetype or "qualification")
        except KeyError as exc:
            skills_summary.append({"skill_id": skill_id, "ok": False, "error": str(exc)})
            continue

        skill_dir = _skill_dir(skill_id)
        current_content = archetype.read_refine_context(skill_dir)
        result = agent_builder.refine_spec(
            archetype_id=archetype.id,
            current_content=current_content,
            feedback=feedback,
            skill_id=skill_id,
            skill_description=skill.description,
        )
        if not result.ok:
            skills_summary.append({"skill_id": skill_id, "ok": False, "error": "; ".join(result.errors)})
            continue

        # Only refine_write_keys are actually derived from result.spec — render_skill_files
        # also returns skill.yaml (and, for conversational, instructions.md/output_contract.json
        # are themselves the writable set) but everything outside that set is left untouched, so
        # rewriting it here would silently discard any hand-edit made in the Files tab for no
        # actual benefit. Correct only what the feedback could have actually changed.
        rendered = archetype.render_skill_files(skill_id, result.spec)
        for rel_path in archetype.refine_write_keys:
            file_path = skill_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(rendered[rel_path], encoding="utf-8")
        skills_summary.append({"skill_id": skill_id, "ok": True, "error": None})

    if not any(s["ok"] for s in skills_summary):
        errors = "; ".join(f"{s['skill_id']}: {s['error']}" for s in skills_summary)
        raise HTTPException(status_code=400, detail=f"Could not produce a valid correction for any skill: {errors}")

    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        return {"status": "saved_with_errors", "agent_id": agent_id, "error": str(exc), "skills": skills_summary}

    status = "ok" if all(s["ok"] for s in skills_summary) else "partial"
    return {"status": status, "agent_id": agent_id, "skills": skills_summary}


def _refine_agent_events(agent_id: str, feedback: str, bundle, refinable_skill_ids: list[str]):
    """Same correction loop as refine_agent, reported step-by-step over SSE
    as each skill is actually corrected — a multi-skill refine over a slow
    shared Ollama host can take minutes, and a bare "Fixing..." spinner
    gives no sense of whether it's stuck or just on skill 2 of 3. This adds
    no extra LLM calls versus the non-streaming endpoint — it's the exact
    same one refine_spec call per skill, just narrated as it happens instead
    of reported all at once at the end.
    """
    total = len(refinable_skill_ids)
    skills_summary = []
    for idx, skill_id in enumerate(refinable_skill_ids, start=1):
        yield _sse({"step": "refine_skill", "status": "start", "skill_id": skill_id, "index": idx, "total": total})
        skill = bundle.skills[skill_id]
        try:
            archetype = get_archetype(skill.archetype or "qualification")
        except KeyError as exc:
            error = str(exc)
            skills_summary.append({"skill_id": skill_id, "ok": False, "error": error})
            yield _sse({
                "step": "refine_skill", "status": "error", "skill_id": skill_id,
                "index": idx, "total": total, "error": error,
            })
            continue

        skill_dir = _skill_dir(skill_id)
        current_content = archetype.read_refine_context(skill_dir)
        result = agent_builder.refine_spec(
            archetype_id=archetype.id,
            current_content=current_content,
            feedback=feedback,
            skill_id=skill_id,
            skill_description=skill.description,
        )
        if not result.ok:
            error = "; ".join(result.errors)
            skills_summary.append({"skill_id": skill_id, "ok": False, "error": error})
            yield _sse({
                "step": "refine_skill", "status": "error", "skill_id": skill_id,
                "index": idx, "total": total, "error": error,
            })
            continue

        rendered = archetype.render_skill_files(skill_id, result.spec)
        for rel_path in archetype.refine_write_keys:
            file_path = skill_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(rendered[rel_path], encoding="utf-8")
        skills_summary.append({"skill_id": skill_id, "ok": True, "error": None})
        yield _sse({"step": "refine_skill", "status": "done", "skill_id": skill_id, "index": idx, "total": total})

    if not any(s["ok"] for s in skills_summary):
        errors = "; ".join(f"{s['skill_id']}: {s['error']}" for s in skills_summary)
        yield _sse({"step": "final", "result": {
            "status": "error", "agent_id": agent_id,
            "error": f"Could not produce a valid correction for any skill: {errors}",
        }})
        return

    yield _sse({"step": "validate", "status": "start"})
    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; files are already saved
        yield _sse({"step": "validate", "status": "error", "error": str(exc)})
        yield _sse({"step": "final", "result": {
            "status": "saved_with_errors", "agent_id": agent_id, "error": str(exc), "skills": skills_summary,
        }})
        return
    yield _sse({"step": "validate", "status": "done"})

    status = "ok" if all(s["ok"] for s in skills_summary) else "partial"
    yield _sse({"step": "final", "result": {"status": status, "agent_id": agent_id, "skills": skills_summary}})


@router.post("/agents/{agent_id}/refine/stream")
def refine_agent_stream(agent_id: str, payload: RefineAgentPayload) -> StreamingResponse:
    """Same as POST /agents/{agent_id}/refine, but streamed over SSE — see
    _refine_agent_events. Validation happens here, synchronously, before the
    stream opens, so a bad request still gets a normal 4xx instead of an
    SSE error event.
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

    refinable_skill_ids = [
        sid for sid in bundle.definition.skills
        if bundle.skills[sid].has_rules or bundle.skills[sid].archetype is not None
    ]
    if not refinable_skill_ids:
        raise HTTPException(status_code=400, detail="This agent has no skills this generator can correct")

    return StreamingResponse(
        _refine_agent_events(agent_id, feedback, bundle, refinable_skill_ids),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_EDIT_FILE_SYSTEM_PROMPT = (
    "You are editing ONE file inside a larger agent configuration, exactly the way a careful "
    "human engineer applies a targeted change. You are given the file's current, complete "
    "content and an instruction describing what to change.\n\n"
    "Output the COMPLETE corrected file, from the very first line to the very last — never a "
    "diff, never a partial snippet, never just the changed lines. Preserve everything the "
    "instruction doesn't mention EXACTLY as it currently is: the same values, wording, comments, "
    "structure, and key order. Do not reformat, reword, delete, or \"improve\" anything you "
    "weren't asked to change — make only the change the instruction actually asks for.\n\n"
    "Output ONLY the raw file content. No code fences, no explanation, no commentary before or "
    "after it."
)


def _build_edit_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    # Generous like agent_builder's own generation timeout — the shared Ollama host is
    # sometimes slow even for a short file, and this is an admin-only, human-in-the-loop
    # action (not a live end-user request), so it can afford to wait.
    return OllamaAdapter(host=host, model="gemma4:12b", timeout_seconds=400, seed=7)


def _strip_wrapping_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        first_line, _, rest = text.partition("\n")
        if first_line and " " not in first_line and len(first_line) < 20:
            text = rest.strip()
    return text


def _resolve_file_key(agent_id: str, file_key: str) -> tuple[Path, str]:
    """Maps a frontend file-tab key (the same scheme AgentEditor.tsx's
    buildSkillGroups already uses — no separate key vocabulary to keep in
    sync) to an actual path plus its syntax kind ("yaml" | "json" |
    "markdown"), the way _read_skill_files resolves them for display: a
    skill's instructions/task_prompt/output_contract filenames come from
    its own skill.yaml manifest, not a hardcoded name.
    """
    if file_key == "agent_yaml":
        return _agent_yaml_path(agent_id), "yaml"

    match = re.match(r"^skill:([^:]+):(.+)$", file_key)
    if not match:
        raise HTTPException(status_code=400, detail=f"Unrecognized file_key '{file_key}'")
    skill_id, rest = match.group(1), match.group(2)
    skill_dir = _skill_dir(skill_id)

    if rest == "skill_yaml":
        return skill_dir / "skill.yaml", "yaml"

    manifest_text = _read_if_exists(skill_dir / "skill.yaml")
    manifest = yaml.safe_load(manifest_text) or {} if manifest_text else {}

    if rest == "instructions_md":
        return skill_dir / manifest.get("instructions", "instructions.md"), "markdown"
    if rest == "task_prompt_md":
        if not manifest.get("task_prompt"):
            raise HTTPException(status_code=400, detail=f"Skill '{skill_id}' has no task_prompt file")
        return skill_dir / manifest["task_prompt"], "markdown"
    if rest == "output_contract_json":
        if not manifest.get("output_contract"):
            raise HTTPException(status_code=400, detail=f"Skill '{skill_id}' has no output_contract file")
        return skill_dir / manifest["output_contract"], "json"
    if rest.startswith("rule:"):
        rule_name = rest[len("rule:"):]
        rel_path = (manifest.get("rules") or {}).get(rule_name)
        if not rel_path:
            raise HTTPException(status_code=400, detail=f"Unknown rule '{rule_name}' for skill '{skill_id}'")
        return skill_dir / rel_path, "yaml"

    raise HTTPException(status_code=400, detail=f"Unrecognized file_key '{file_key}'")


class EditFilePayload(BaseModel):
    file_key: str
    feedback: str


@router.post("/agents/{agent_id}/edit-file")
def edit_file_with_ai(agent_id: str, payload: EditFilePayload) -> dict:
    """The one "Fix with AI" mechanism — edits exactly the file you have
    open, the way a careful human applies a targeted change: the model
    sees that file's full current content and your instruction, and must
    output the complete corrected file, preserving everything it wasn't
    asked to touch. Works on every file (agent.yaml, skill.yaml,
    instructions.md, output_contract.json, every rules/*.yaml) and on
    live agents as much as drafts — there is no separate whole-agent
    regeneration path.

    Unlike the archetype-driven generate/refine flow elsewhere in this
    module, the LLM writes this file's raw text directly rather than
    filling a schema that Python renders deterministically. Two safety
    nets stand in for that constraint: the result must parse as valid
    YAML/JSON (skipped for Markdown, which has no syntax to violate), and
    the agent must still load cleanly afterward — if either fails,
    nothing is silently left broken; the caller is told.
    """
    try:
        load_agent(agent_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    feedback = payload.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="feedback must not be empty")

    file_path, kind = _resolve_file_key(agent_id, payload.file_key)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path.name}")
    current_text = file_path.read_text(encoding="utf-8")

    adapter = _build_edit_adapter()
    user_prompt = f"Current content of {file_path.name}:\n\n{current_text}\n\nInstruction: {feedback}"
    try:
        new_text, _meta = adapter.generate_text(
            system_prompt=_EDIT_FILE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.1,
        )
    except OllamaContentError:
        # The HTTP call succeeded but the model burned its whole output budget on internal
        # reasoning and never emitted content (seen in practice with gemma4:12b) — not a
        # transport failure, so worth one retry rather than failing outright.
        try:
            new_text, _meta = adapter.generate_text(
                system_prompt=_EDIT_FILE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.1,
            )
        except OllamaError as exc:
            raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    new_text = _strip_wrapping_fence(new_text)
    if not new_text:
        raise HTTPException(status_code=400, detail="The correction came back empty — nothing was saved.")
    new_text = new_text + ("\n" if not new_text.endswith("\n") else "")

    if kind == "yaml":
        try:
            yaml.safe_load(new_text)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail=f"Correction produced invalid YAML, not saved: {exc}")
    elif kind == "json":
        try:
            json.loads(new_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Correction produced invalid JSON, not saved: {exc}")

    file_path.write_text(new_text, encoding="utf-8")

    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; the file is already saved
        return {"status": "saved_with_errors", "agent_id": agent_id, "file_key": payload.file_key, "error": str(exc)}

    return {"status": "ok", "agent_id": agent_id, "file_key": payload.file_key}


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
    api_keys.delete_key(agent_id)
    return {"status": "deleted", "agent_id": agent_id}


@router.get("/agents/{agent_id}/api-key")
def get_agent_api_key(agent_id: str) -> dict:
    """Returns the key a client uses to call POST /agents/{agent_id}/invoke
    (the public, non-admin endpoint). Created on first request if this
    agent predates API keys.
    """
    if not _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {"agent_id": agent_id, "api_key": api_keys.get_or_create_key(agent_id)}


@router.post("/agents/{agent_id}/api-key/regenerate")
def regenerate_agent_api_key(agent_id: str) -> dict:
    """Invalidates the old key immediately — any site already using it stops
    working until it's updated with the new one.
    """
    if not _agent_yaml_path(agent_id).exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {"agent_id": agent_id, "api_key": api_keys.regenerate_key(agent_id)}


class SetInputModePayload(BaseModel):
    input_mode: Literal["chat", "form", "json", "trigger"]


@router.post("/agents/{agent_id}/input-mode")
def set_input_mode(agent_id: str, payload: SetInputModePayload) -> dict:
    """Lets Playground change which interface an agent defaults to without
    anyone hand-editing agent.yaml — a targeted line replace/insert, same
    approach the frontend's "Accept draft" button uses for draft/routable.
    """
    yaml_path = _agent_yaml_path(agent_id)
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")

    text = yaml_path.read_text(encoding="utf-8")
    new_line = f"input_mode: {payload.input_mode}"
    if re.search(r"^input_mode:.*$", text, flags=re.M):
        text = re.sub(r"^input_mode:.*$", new_line, text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(agent_id:.*)$", rf"\1\n{new_line}", text, count=1, flags=re.M)
    yaml_path.write_text(text, encoding="utf-8")

    evict(agent_id)
    try:
        load_agent(agent_id, force_reload=True)
    except Exception as exc:  # noqa: BLE001 - report, don't hide; the file is already rewritten
        return {"status": "saved_with_errors", "agent_id": agent_id, "error": str(exc)}
    return {"status": "ok", "agent_id": agent_id, "input_mode": payload.input_mode}


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


def _stage_trace(ctx) -> list[dict[str, Any]]:
    """Every stage this run actually went through, in order — the Playground's
    "AI Observation" column source, same shape as the chat layer's stage_trace.

    `detail` carries what an LLM stage actually did: the model, token counts,
    its tool calls, and the model's own reasoning trace. Non-LLM stages set
    nothing and get an empty dict.
    """
    return [
        {
            "stage": r.stage, "status": r.status, "summary": r.summary,
            "duration_ms": r.duration_ms, "detail": r.detail,
        }
        for r in ctx.stage_results
    ]


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
        "stage_trace": _stage_trace(ctx),
    }


@router.post("/agents/{agent_id}/test-run-file")
async def test_run_agent_file(agent_id: str, file: UploadFile = File(...)) -> dict:
    """The file-upload counterpart to /test-run — for input_mode: "file"
    agents (currently just fin_health). Parses the uploaded report into an
    evidence dict via excel_ingest, then runs the exact same pipeline any
    other input path would. Only understands the one due-diligence report
    format excel_ingest.py targets; a differently-shaped spreadsheet will
    fail here with whatever ValueError parsing it raises.
    """
    try:
        raw_bytes = await file.read()
        evidence = excel_ingest.parse_fin_health_excel(raw_bytes, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        ctx = invoke_agent(agent_id, {"evidence": evidence})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {
        "run_id": ctx.run_id,
        "parsed_evidence": evidence,
        "decision": ctx.decision,
        "explanation": ctx.explanation,
        "error": ctx.error,
        "stage_trace": _stage_trace(ctx),
    }


class ChatPayload(BaseModel):
    session_id: str | None = None
    message: str
    # The Playground's colloquial-style toggle. Defaults on, so a client that
    # doesn't know about it gets the shipped behaviour.
    style: bool = True
    # Spoken-answer mode: short, no markdown. Defaults off — it is the voice
    # client that turns this on, and the Playground toggle exists so the
    # result can be checked without wiring one up.
    voice: bool = False


def _chat_stream_events(agent_id: str, payload: "ChatPayload"):
    """The Playground's chat turn, with sentences emitted as they are written.

    Same session handling as the plain route — it calls the same
    handle_chat_turn — so a conversation can move between the two without
    losing its thread.

    Streaming is not tied to voice mode. Voice changes how an answer is
    written; this changes when the client sees it. Watching a Tamil answer
    build sentence by sentence is also the only way to check the splitter
    without wiring up a voice client, which is most of why this exists.
    """
    events: queue.Queue = queue.Queue()
    outcome: dict[str, Any] = {}

    def sink(text: str, _language: str | None) -> None:
        events.put({"event": "sentence", "text": text})

    def worker() -> None:
        speech_stream.sentence_sink.set(sink)
        try:
            outcome["result"] = chat.handle_chat_turn(
                agent_id, payload.session_id, payload.message, payload.style, payload.voice)
        except FileNotFoundError:
            outcome["error"] = f"Unknown agent_id '{agent_id}'"
        except Exception as exc:                # noqa: BLE001 - relayed below
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            events.put(None)

    # copy_context, or the sink set above never reaches the pipeline running
    # inside this thread — which looks exactly like "streaming did nothing".
    threading.Thread(target=copy_context().run, args=(worker,), daemon=True).start()

    started = time.perf_counter()
    index = 0
    while True:
        event = events.get()
        if event is None:
            break
        index += 1
        event["index"] = index
        event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        yield _sse(event)

    if "error" in outcome:
        yield _sse({"event": "error", "message": outcome["error"]})
        return

    # Every field the plain /chat route returns, so ChatTurnResult means the
    # same thing on both and a client can switch without special-casing.
    result = outcome["result"]
    yield _sse({
        "event": "done",
        "session_id": result.session_id,
        "reply": result.reply,
        "evidence": result.evidence,
        "content_type": result.content_type,
        "decision": result.decision,
        "stage_trace": result.stage_trace,
        "done": result.done,
    })


@router.post("/agents/{agent_id}/chat/stream")
def chat_with_agent_stream(agent_id: str, payload: ChatPayload) -> StreamingResponse:
    """Streaming counterpart to /chat, for the Playground.

    Emits `sentence` events as the answer is written, then a `done` event
    carrying exactly what the plain route returns — same reply, same decision,
    same stage trace — so the client renders sentences early and then replaces
    them with the authoritative reply.
    """
    return StreamingResponse(
        _chat_stream_events(agent_id, payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agents/{agent_id}/chat")
def chat_with_agent(agent_id: str, payload: ChatPayload) -> dict:
    """No API key required — this is the internal Playground's chat mode,
    same trust level as test-run above. The public, key-gated equivalent for
    a client's own site is POST /agents/{agent_id}/chat in backend/main.py.
    """
    try:
        result = chat.handle_chat_turn(
            agent_id, payload.session_id, payload.message, payload.style, payload.voice)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    return {
        "session_id": result.session_id, "reply": result.reply,
        "evidence": result.evidence, "decision": result.decision, "done": result.done,
        "content_type": result.content_type, "stage_trace": result.stage_trace,
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

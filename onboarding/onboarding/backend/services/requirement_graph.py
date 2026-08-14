"""
Requirement Graph engine -- replaces the old hand-maintained state machine.
See Phase 2 of YONO_3.0_Backend_Redesign_BuildPrompts.md.

Key invariant: Application.status and Application.get_progress() are NEVER
stored/updated independently of Requirement state -- they are always
recomputed here from the current set of Requirement rows.
"""
import logging
from datetime import datetime

from backend.models.models import Requirement, RequirementState, ReviewItem, ApplicationStatus
from backend.services import product_catalog, validators, events

logger = logging.getLogger("yono.requirement_graph")

STATE = RequirementState

# mobile OTP requirement types: while VERIFYING, these are still awaiting
# actionable user input (the OTP code itself), unlike document/other types
# whose VERIFYING state means "waiting on an async job" with no further
# input expected from the user. get_next_requirement() below treats these
# as still "next" while VERIFYING for exactly that reason.
_MOBILE_TYPES = {"mobile_otp", "guardian_mobile_otp"}


def _is_applicable(req_def: dict, product_customer_type: str) -> bool:
    cond = req_def.get("applicable_if")
    if not cond:
        return True
    # simple "key=value" condition language, e.g. "customer_type=minor"
    key, _, val = cond.partition("=")
    if key.strip() == "customer_type":
        return product_customer_type == val.strip()
    return True


def instantiate_requirements(application, db):
    """Creates Requirement rows for a new Application based on its
    product's catalog entry. Called once at Application creation."""
    product = product_catalog.get_product(application.product_id)
    customer_type = product.get("customer_type", "individual")
    created = []
    for req_def in product["requirements"]:
        if not _is_applicable(req_def, customer_type):
            continue
        label = req_def["label"]
        if req_def.get("doc_name"):
            label = f"Upload {req_def['doc_name']}"
        req = Requirement(
            application_id=application.id,
            type=req_def["type"],
            label=label,
            format_hint=req_def.get("format_hint"),
            mapped_step=req_def.get("mapped_step", 1),
            state=STATE.NOT_STARTED.value,
            depends_on=req_def.get("depends_on", []),
            escalation_threshold=req_def.get("escalation_threshold", 2),
        )
        db.add(req)
        created.append(req)
    db.flush()
    return created


def _dependencies_met(requirement, all_requirements):
    if not requirement.depends_on:
        return True
    verified_types = {r.type for r in all_requirements if r.state == STATE.VERIFIED.value}
    return all(dep in verified_types for dep in requirement.depends_on)


def get_next_requirement(application, scope=None):
    """Earliest NOT_STARTED/AWAITING_INPUT/REJECTED Requirement in natural
    (catalog) order, respecting depends_on (dependencies must be VERIFIED).
    If `scope` is given (e.g. "guardian"), only considers Requirements
    whose type is guardian-scoped (guardian_consent, guardian_mobile_otp)."""
    reqs = sorted(application.requirements, key=lambda r: (r.mapped_step, r.created_at))
    guardian_types = {"guardian_consent", "guardian_mobile_otp"}
    for r in reqs:
        if scope == "guardian" and r.type not in guardian_types:
            continue
        if scope is None and r.type in guardian_types:
            # guardian requirements are only ever surfaced via a guardian-scoped session
            continue
        matches = r.state in (STATE.NOT_STARTED.value, STATE.AWAITING_INPUT.value, STATE.REJECTED.value)
        if not matches and r.type in _MOBILE_TYPES and r.state == STATE.VERIFYING.value:
            # mobile OTP requirements awaiting the user's OTP code entry --
            # see _MOBILE_TYPES comment above for why this differs from the
            # general VERIFYING semantics of other requirement types.
            matches = True
        if matches:
            if _dependencies_met(r, reqs):
                return r
    return None


def compute_progress(application):
    reqs = sorted(application.requirements, key=lambda r: (r.mapped_step, r.created_at))
    steps = {}
    for r in reqs:
        idx = r.mapped_step
        steps.setdefault(idx, []).append(r)

    step_list = []
    current_step = None
    for idx in sorted(steps.keys()):
        members = steps[idx]
        states = {m.state for m in members}
        if states <= {STATE.VERIFIED.value}:
            status = "complete"
        elif states & {STATE.REJECTED.value, STATE.ESCALATED.value}:
            status = "action_needed"
        elif states & {STATE.SUBMITTED.value, STATE.VERIFYING.value}:
            status = "in_progress"
        elif states & {STATE.AWAITING_INPUT.value}:
            status = "in_progress"
        else:
            status = "pending"
        step_list.append({
            "index": idx,
            "label": ", ".join(sorted({m.label for m in members})),
            "status": status,
        })
        if current_step is None and status != "complete":
            current_step = idx

    total_steps = max(steps.keys()) if steps else 5
    if current_step is None:
        current_step = total_steps
    return {"current_step": current_step, "total_steps": total_steps, "steps": step_list}


def compute_application_status(application):
    reqs = application.requirements
    if not reqs:
        return ApplicationStatus.IN_PROGRESS.value
    states = [r.state for r in reqs]
    if any(s in (STATE.REJECTED.value, STATE.ESCALATED.value) for s in states):
        return ApplicationStatus.ACTION_NEEDED.value
    if all(s == STATE.VERIFIED.value for s in states):
        return ApplicationStatus.APPROVED.value
    if any(s in (STATE.VERIFYING.value, STATE.SUBMITTED.value) for s in states):
        return ApplicationStatus.UNDER_REVIEW.value
    return ApplicationStatus.IN_PROGRESS.value


def _sync_application_status(application, db):
    new_status = compute_application_status(application)
    if application.status != new_status:
        old = application.status
        application.status = new_status
        application.updated_at = datetime.utcnow()
        db.flush()
        events.emit("application_status_changed", {
            "application_id": application.id, "old_status": old, "new_status": new_status,
        })


def _escalate(requirement, db, reason="failure_threshold_reached"):
    logger.warning(
        "[requirement_graph] escalating requirement id=%s type=%s application_id=%s failure_count=%s reason=%s",
        requirement.id, requirement.type, requirement.application_id, requirement.failure_count, reason,
    )
    requirement.state = STATE.ESCALATED.value
    db.flush()
    item = ReviewItem(
        application_id=requirement.application_id,
        type="kyc_review",
        reason=f"{requirement.type} failed {requirement.failure_count} times: {reason}",
        requirement_id=requirement.id,
        status="open",
    )
    db.add(item)
    db.flush()
    events.emit("hitl_item_added", {"item_id": item.id, "application_id": item.application_id, "type": item.type})
    return item


# requirement types resolved instantly (no async document-review step)
_INSTANT_TYPES = {"pan", "business_pan", "gstin", "authorized_signatory",
                   "guardian_consent", "product_confirm", "review_submit"}
# _MOBILE_TYPES is defined near the top of this module (used by
# get_next_requirement too) -- referenced here, not redefined.


def submit_requirement_value(application, requirement_id, value, db):
    """Validates `value` against the requirement's type-specific validator,
    transitions state accordingly, increments failure_count on rejection,
    auto-creates a ReviewItem when failure_count hits escalation_threshold.

    Returns dict: {ok, requirement, error (optional), escalated (bool)}
    """
    requirement = next((r for r in application.requirements if r.id == requirement_id), None)
    if requirement is None:
        logger.warning("[requirement_graph] submit_requirement_value: unknown requirement_id=%s application_id=%s", requirement_id, application.id)
        return {"ok": False, "error": "requirement_not_found"}

    logger.info(
        "[requirement_graph] submit_requirement_value: application_id=%s requirement_id=%s type=%s current_state=%s",
        application.id, requirement.id, requirement.type, requirement.state,
    )

    if requirement.state == STATE.VERIFIED.value:
        # Only edit_verified_requirement() may touch an already-VERIFIED requirement.
        logger.warning("[requirement_graph] rejected: requirement_id=%s already VERIFIED, use edit_verified_requirement", requirement.id)
        return {"ok": False, "error": "requirement_already_verified"}

    reqs = application.requirements
    if not _dependencies_met(requirement, reqs):
        logger.warning("[requirement_graph] rejected: requirement_id=%s dependencies_not_met (depends_on=%s)", requirement.id, requirement.depends_on)
        return {"ok": False, "error": "dependencies_not_met"}

    rtype = requirement.type

    # --- mobile OTP two-phase handling ---
    if rtype in _MOBILE_TYPES:
        digits = (value or "").strip()
        looks_like_otp = len(digits) == 6 and digits.isdigit() and requirement.state == STATE.VERIFYING.value
        if looks_like_otp:
            from backend.services.otp import verify_otp
            result = verify_otp(requirement, digits)
            if result["ok"]:
                requirement.state = STATE.VERIFIED.value
                requirement.updated_at = datetime.utcnow()
                db.flush()
                logger.info("[requirement_graph] OTP verified: requirement_id=%s application_id=%s", requirement.id, application.id)
                events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": application.id, "state": requirement.state})
                _sync_application_status(application, db)
                return {"ok": True, "requirement": requirement}
            else:
                requirement.failure_count += 1
                escalated = False
                logger.warning("[requirement_graph] OTP verification failed: requirement_id=%s reason=%s failure_count=%s", requirement.id, result["error"], requirement.failure_count)
                if requirement.failure_count >= requirement.escalation_threshold:
                    _escalate(requirement, db, reason=result["error"])
                    escalated = True
                db.flush()
                return {"ok": False, "requirement": requirement, "error": result["error"], "escalated": escalated}
        else:
            ok, err = validators.validate_mobile(digits)
            if ok:
                requirement.value = digits[-4:].rjust(len(digits), "*")
                requirement.state = STATE.VERIFYING.value
                from backend.services.otp import generate_otp
                generate_otp(application, requirement, db)
                db.flush()
                logger.info("[requirement_graph] mobile number accepted, OTP dispatch triggered: requirement_id=%s application_id=%s", requirement.id, application.id)
                events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": application.id, "state": requirement.state})
                _sync_application_status(application, db)
                return {"ok": True, "requirement": requirement, "otp_sent": True}
            else:
                requirement.failure_count += 1
                escalated = False
                logger.warning("[requirement_graph] mobile number rejected: requirement_id=%s reason=%s failure_count=%s", requirement.id, err, requirement.failure_count)
                if requirement.failure_count >= requirement.escalation_threshold:
                    _escalate(requirement, db, reason=err)
                    escalated = True
                db.flush()
                return {"ok": False, "requirement": requirement, "error": err, "escalated": escalated}

    # --- instant-validation types ---
    if rtype in _INSTANT_TYPES:
        validator = validators.get_validator(rtype)
        ok, err = validator(value)
        if ok:
            requirement.value = value.strip().upper() if rtype in ("pan", "business_pan", "gstin") else value.strip()
            requirement.state = STATE.VERIFIED.value
            requirement.updated_at = datetime.utcnow()
            db.flush()
            logger.info("[requirement_graph] instant-type verified: requirement_id=%s type=%s application_id=%s", requirement.id, rtype, application.id)
            events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": application.id, "state": requirement.state})
            _sync_application_status(application, db)
            return {"ok": True, "requirement": requirement}
        else:
            requirement.failure_count += 1
            escalated = False
            logger.warning("[requirement_graph] instant-type validation failed: requirement_id=%s type=%s reason=%s failure_count=%s", requirement.id, rtype, err, requirement.failure_count)
            if requirement.failure_count >= requirement.escalation_threshold:
                _escalate(requirement, db, reason=err)
                escalated = True
            db.flush()
            events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": application.id, "state": requirement.state})
            return {"ok": False, "requirement": requirement, "error": err, "escalated": escalated}

    # --- document type: handled via POST /applications/{id}/documents, not here ---
    if rtype == "document":
        return {"ok": False, "error": "documents_must_be_submitted_via_documents_endpoint"}

    logger.error("[requirement_graph] unhandled requirement type encountered: %s (requirement_id=%s)", rtype, requirement.id)
    return {"ok": False, "error": f"unhandled_requirement_type:{rtype}"}


def edit_verified_requirement(application, requirement_id, new_value, db):
    """The explicit user-initiated 'Edit' path from the Review screen. This
    is the ONLY legitimate way to touch an already-VERIFIED requirement --
    re-validates and re-triggers verification (back to SUBMITTED/VERIFYING)
    rather than accepting the new value provisionally."""
    requirement = next((r for r in application.requirements if r.id == requirement_id), None)
    if requirement is None:
        return {"ok": False, "error": "requirement_not_found"}
    if requirement.state != STATE.VERIFIED.value:
        return {"ok": False, "error": "only_verified_requirements_can_be_edited"}

    rtype = requirement.type
    if rtype in _MOBILE_TYPES:
        ok, err = validators.validate_mobile(new_value)
        if not ok:
            return {"ok": False, "error": err}
        requirement.value = new_value
        requirement.state = STATE.VERIFYING.value
        from backend.services.otp import generate_otp
        generate_otp(application, requirement, db)
    else:
        validator = validators.get_validator(rtype)
        ok, err = validator(new_value)
        if not ok:
            return {"ok": False, "error": err}
        requirement.value = new_value
        requirement.state = STATE.SUBMITTED.value if rtype == "document" else STATE.VERIFIED.value

    requirement.updated_at = datetime.utcnow()
    db.flush()
    events.emit("requirement_updated", {"requirement_id": requirement.id, "application_id": application.id, "state": requirement.state, "edited": True})
    _sync_application_status(application, db)
    return {"ok": True, "requirement": requirement}

"""
Unit tests for the Requirement Graph engine (Phase 2). Covers the four
scenarios called out in the spec:
  1. savings-account Application progressing through requirements in order
  2. MSME Application with extra requirements
  3. guardian-applicable Application including guardian requirements
  4. PAN submission failing twice -> escalation

OTP codes are hashed at rest (Phase 6), so tests use a small introspection
helper (`_known_code_for`) to set a known code+hash directly on the
Requirement row rather than trying to reverse a SHA-256 hash -- this
mirrors what a demo script does by reading the code from server logs.
"""
import hashlib
from datetime import datetime, timedelta

from backend.models import models as m
from backend.services import requirement_graph as rg
from backend.tests.conftest import make_application


def _known_code_for(requirement, code="123456"):
    requirement.otp_code_hash = hashlib.sha256(code.encode()).hexdigest()
    requirement.otp_expires_at = datetime.utcnow() + timedelta(seconds=300)
    return code


def _verify_mobile_requirement(app, req, db, mobile="9876543210"):
    res = rg.submit_requirement_value(app, req.id, mobile, db)
    assert res["ok"] and res.get("otp_sent"), res
    db.refresh(req)
    assert req.state == "VERIFYING"
    code = _known_code_for(req)
    ver = rg.submit_requirement_value(app, req.id, code, db)
    assert ver["ok"], ver
    db.refresh(req)
    assert req.state == "VERIFIED"


def test_savings_happy_path(db):
    app = make_application(db, "savings_account")
    reqs = {r.type: r for r in app.requirements}
    assert set(reqs.keys()) == {"mobile_otp", "pan", "document", "product_confirm", "review_submit"}

    nxt = rg.get_next_requirement(app)
    assert nxt.type == "mobile_otp"
    _verify_mobile_requirement(app, nxt, db)

    nxt2 = rg.get_next_requirement(app)
    assert nxt2.type == "pan"
    res2 = rg.submit_requirement_value(app, nxt2.id, "ABCDE1234F", db)
    assert res2["ok"], res2
    db.refresh(nxt2)
    assert nxt2.state == "VERIFIED"

    nxt3 = rg.get_next_requirement(app)
    assert nxt3.type == "document"

    progress = rg.compute_progress(app)
    assert progress["total_steps"] == 5
    assert progress["current_step"] in (2, 3)


def test_msme_extra_requirements(db):
    app = make_application(db, "msme_current_account")
    reqs = {r.type: r for r in app.requirements}
    assert "gstin" in reqs and "business_pan" in reqs and "authorized_signatory" in reqs

    nxt = rg.get_next_requirement(app)
    assert nxt.type == "mobile_otp"
    _verify_mobile_requirement(app, nxt, db)

    nxt2 = rg.get_next_requirement(app)
    assert nxt2.type in ("pan", "business_pan")

    gstin_req = reqs["gstin"]
    res = rg.submit_requirement_value(app, gstin_req.id, "22AAAAA0000A1Z5", db)
    assert res["ok"] is False
    assert res["error"] == "dependencies_not_met"

    bp_req = reqs["business_pan"]
    res_bp = rg.submit_requirement_value(app, bp_req.id, "ABCDE1234F", db)
    assert res_bp["ok"], res_bp

    res_gstin = rg.submit_requirement_value(app, gstin_req.id, "22AAAAA0000A1Z5", db)
    assert res_gstin["ok"], res_gstin
    db.refresh(gstin_req)
    assert gstin_req.state == "VERIFIED"


def test_guardian_applicable_flow(db):
    app = make_application(db, "minor_savings_account")
    reqs = {r.type: r for r in app.requirements}
    assert "guardian_consent" in reqs
    assert "guardian_mobile_otp" in reqs

    nxt = rg.get_next_requirement(app, scope=None)
    assert nxt.type == "mobile_otp"
    _verify_mobile_requirement(app, nxt, db)

    nxt_main = rg.get_next_requirement(app, scope=None)
    assert nxt_main is None or nxt_main.type not in ("guardian_consent", "guardian_mobile_otp")

    nxt_guardian = rg.get_next_requirement(app, scope="guardian")
    assert nxt_guardian.type == "guardian_consent"

    res = rg.submit_requirement_value(app, nxt_guardian.id, "guardian is verified parent", db)
    assert res["ok"], res
    db.refresh(nxt_guardian)
    assert nxt_guardian.state == "VERIFIED"

    nxt_guardian2 = rg.get_next_requirement(app, scope="guardian")
    assert nxt_guardian2.type == "guardian_mobile_otp"


def test_pan_fails_twice_escalates(db):
    app = make_application(db, "savings_account")
    reqs = {r.type: r for r in app.requirements}
    mobile_req = reqs["mobile_otp"]
    _verify_mobile_requirement(app, mobile_req, db)

    pan_req = reqs["pan"]
    db.refresh(pan_req)
    assert pan_req.escalation_threshold == 2

    r1 = rg.submit_requirement_value(app, pan_req.id, "FAILFAILFF", db)
    assert r1["ok"] is False
    db.refresh(pan_req)
    assert pan_req.failure_count == 1
    assert pan_req.state != "ESCALATED"

    r2 = rg.submit_requirement_value(app, pan_req.id, "FAILFAILFF", db)
    assert r2["ok"] is False
    db.refresh(pan_req)
    assert pan_req.failure_count == 2
    assert pan_req.state == "ESCALATED"

    open_items = db.query(m.ReviewItem).filter_by(application_id=app.id, type="kyc_review").all()
    assert len(open_items) == 1
    assert open_items[0].status == "open"

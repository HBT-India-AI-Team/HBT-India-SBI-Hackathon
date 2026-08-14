"""
Format validators + deterministic debug-hook test values for KYC fields.

See /docs/DEBUG_HOOKS.md for the full documented list -- this module is the
single source of truth those docs describe, referenced from Phase 2's
requirement_graph.py (submit_requirement_value) and Phase 6's OTP module.
"""
import re

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

# --- KNOWN TEST VALUES (Phase 5 debug hooks) ---
# These override normal format checking so demo/test flows can deterministically
# hit reject/escalation paths instead of relying on random outcomes.
MOBILE_ALWAYS_FAIL_ALREADY_REGISTERED = "0000000000"
PAN_ALWAYS_FAIL_FORMAT = "FAILFAILFF"
GSTIN_ALWAYS_FAIL_FORMAT = "FAILGSTIN00"
PAN_ALWAYS_PASS = "ABCDE1234F"
GSTIN_ALWAYS_PASS = "22AAAAA0000A1Z5"

# Document-type sanity check debug hook (see doc_parser.classify_document
# and the document upload endpoint in routers/applications.py). Either of
# these forces classify_document() to deterministically report a mismatch,
# regardless of what the heuristic/VLM check would otherwise conclude, so
# the rejection path can be demoed reliably:
#   - debug_outcome=="reject" passed on the upload (reused from the
#     existing document_review job debug hook)
#   - the uploaded file's original filename contains this marker
#     (case-insensitive), e.g. "wrong_doc_type_selfie.jpg"
DOC_MISMATCH_FILENAME_MARKER = "wrong_doc_type"


def validate_mobile(value: str):
    """Returns (ok: bool, error: str|None)."""
    if value == MOBILE_ALWAYS_FAIL_ALREADY_REGISTERED:
        return False, "mobile_already_registered"
    if not value or not MOBILE_RE.match(value.strip()):
        return False, "invalid_format"
    return True, None


def validate_pan(value: str):
    if value == PAN_ALWAYS_FAIL_FORMAT:
        return False, "invalid_format"
    if not value or not PAN_RE.match(value.strip().upper()):
        return False, "invalid_format"
    return True, None


def validate_gstin(value: str):
    if value == GSTIN_ALWAYS_FAIL_FORMAT:
        return False, "invalid_format"
    if not value or not GSTIN_RE.match(value.strip().upper()):
        return False, "invalid_format"
    return True, None


def validate_otp_format(value: str):
    if not value or not re.match(r"^\d{6}$", value.strip()):
        return False, "invalid_format"
    return True, None


def validate_free_text(value: str):
    if not value or not value.strip():
        return False, "empty"
    return True, None


def validate_yes_no(value: str):
    if not value or value.strip().lower() not in ("yes", "no", "y", "n"):
        return False, "invalid_format"
    return True, None


VALIDATORS = {
    "mobile_otp": validate_mobile,
    "guardian_mobile_otp": validate_mobile,
    "pan": validate_pan,
    "business_pan": validate_pan,
    "gstin": validate_gstin,
    "authorized_signatory": validate_free_text,
    "guardian_consent": validate_free_text,
    "product_confirm": validate_yes_no,
    "review_submit": validate_yes_no,
}


def get_validator(requirement_type: str):
    return VALIDATORS.get(requirement_type, validate_free_text)

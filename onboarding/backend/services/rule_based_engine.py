"""
Simple rule-based message handler (Phase 3). Takes the Application's
get_next_requirement(), emits its templated prompt, and on the next
inbound message attempts to match it against that requirement's expected
input shape, calling submit_requirement_value(). Deliberately simple --
this becomes the automatic FALLBACK once Phase 8 adds the LLM (see
onboarding_llm.py), so it is not over-built.
"""
from backend.services import requirement_graph as rg

PROMPT_TEMPLATES = {
    "mobile_otp": "Please share your 10-digit mobile number to get started.",
    "guardian_mobile_otp": "Please share the guardian's 10-digit mobile number.",
    "pan": "Please share your PAN (format: ABCDE1234F).",
    "business_pan": "Please share your business PAN (format: ABCDE1234F).",
    "gstin": "Please share your business GSTIN (format: 22AAAAA0000A1Z5).",
    "authorized_signatory": "Please share the authorized signatory's full name.",
    "guardian_consent": "Please share the guardian's relationship to the minor to record consent (e.g. 'parent').",
    "document": "Please upload the required document: {label}.",
    "product_confirm": "Shall we proceed with this product? (yes/no)",
    "review_submit": "Ready to submit your application for review? (yes/no)",
}

OTP_PROMPT = "We've sent a 6-digit code. Please enter it to verify."


def _prompt_for(requirement):
    if requirement.state == "VERIFYING" and requirement.type in ("mobile_otp", "guardian_mobile_otp"):
        return OTP_PROMPT
    template = PROMPT_TEMPLATES.get(requirement.type, f"Please provide: {requirement.label}")
    return template.format(label=requirement.label)


def handle_message(application, text, db, scope=None):
    """Returns {reply_text, actions_applied: [...], progress}."""
    text = (text or "").strip()
    next_req = rg.get_next_requirement(application, scope=scope)

    if next_req is None:
        return {
            "reply_text": "You're all caught up! There's nothing pending right now.",
            "actions_applied": [],
            "progress": rg.compute_progress(application),
        }

    # If the requirement is awaiting an OTP, or NOT_STARTED/AWAITING_INPUT and
    # the user just sent something, try to apply it as this requirement's value.
    if text:
        result = rg.submit_requirement_value(application, next_req.id, text, db)
        if result.get("ok"):
            db.refresh(application)
            new_next = rg.get_next_requirement(application, scope=scope)
            if result.get("otp_sent"):
                reply = OTP_PROMPT
            elif new_next is not None:
                reply = f"Got it, thanks. {_prompt_for(new_next)}"
            else:
                reply = "Thanks -- that completes everything we need for now."
            return {
                "reply_text": reply,
                "actions_applied": [{"requirement_id": next_req.id, "type": next_req.type, "result": "accepted"}],
                "progress": rg.compute_progress(application),
            }
        else:
            error = result.get("error")
            if error == "dependencies_not_met":
                return {
                    "reply_text": _prompt_for(next_req),
                    "actions_applied": [],
                    "progress": rg.compute_progress(application),
                }
            if result.get("escalated"):
                reply = (
                    "That still doesn't look right, so we've flagged this for our support "
                    "team to take a look. They'll reach out shortly."
                )
            else:
                reply = f"That didn't look right ({error}). {_prompt_for(next_req)}"
            return {
                "reply_text": reply,
                "actions_applied": [{"requirement_id": next_req.id, "type": next_req.type, "result": "rejected", "error": error}],
                "progress": rg.compute_progress(application),
            }

    return {
        "reply_text": _prompt_for(next_req),
        "actions_applied": [],
        "progress": rg.compute_progress(application),
    }

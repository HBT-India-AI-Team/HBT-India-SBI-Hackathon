"""
Phase 7: vision-assisted (NOT authoritative) document field extraction, plus
a document-type sanity check (classify_document()).

# MOCK: real VLM extraction (auto-discovered/configured Ollama vision
# model) is now attempted for real by extract_fields() below too (real
# call, real timeout, real try/except -- via _try_vlm_extract(), the same
# pattern _try_vlm_classify() already used for classify_document()) but
# WILL fail in this dev sandbox -- there's no network path to the
# configured OLLAMA_BASE_URL from here (see /docs/MOCKS.md and
# backend/scripts/check_ollama_connectivity.py). The canned fallback path
# is what actually gets exercised/tested in this build. extract_fields()
# never blocks/fails the upload flow either way -- it is
# informational/assistive only, unlike classify_document() below.

classify_document(), unlike extract_fields(), DOES gate upload outcome: if
it reports matches_expected=False, the caller
(POST /applications/{id}/documents in routers/applications.py) rejects the
document via the same rejection path the scheduled document_review job
uses (backend/services/scheduler.py::apply_document_rejection), instead of
silently accepting whatever was uploaded. See /docs/MOCKS.md for the debug
hook that forces a deterministic mismatch for demo/test purposes.
"""
import base64
import json
import logging
import os

import httpx

from backend import config
from backend.services import validators

logger = logging.getLogger("yono.doc_parser")

_CANNED_BY_LABEL = {
    "pan": {"name": "SAMPLE NAME", "pan_number": "ABCDE1234F", "date_of_birth": "1990-01-01"},
    "gst": {"legal_name": "SAMPLE BUSINESS PVT LTD", "gstin": "22AAAAA0000A1Z5", "registration_date": "2020-01-01"},
    "guardian": {"name": "SAMPLE GUARDIAN", "id_number": "XXXX-XXXX-1234"},
}


def _canned_for_label(doc_label: str) -> dict:
    label_lower = (doc_label or "").lower()
    if "pan" in label_lower:
        return _CANNED_BY_LABEL["pan"]
    if "gst" in label_lower:
        return _CANNED_BY_LABEL["gst"]
    if "guardian" in label_lower:
        return _CANNED_BY_LABEL["guardian"]
    return {"note": "no extraction template for this document type"}


def _try_vlm_extract(file_ref: str, doc_label: str) -> dict:
    """Real attempt at VLM-based field extraction via Ollama. Same pattern
    as _try_vlm_classify() above: genuine network call (base64-encodes the
    image, hits {OLLAMA_BASE_URL}/api/generate with a vision-capable
    model), EXPECTED to raise in this dev sandbox (no network path to the
    configured endpoint) -- the caller (extract_fields) falls back to the
    canned dict on any exception. On a machine with real Ollama
    connectivity, this is the live code path.

    The target JSON schema per document type is derived from
    _CANNED_BY_LABEL's keys/shapes (e.g. pan -> {name, pan_number,
    date_of_birth}, gst -> {legal_name, gstin, registration_date},
    guardian -> {name, id_number})."""
    model = config.OLLAMA_VISION_MODEL or config.OLLAMA_MODEL
    if not model:
        raise RuntimeError("no OLLAMA_VISION_MODEL/OLLAMA_MODEL configured")

    template = _canned_for_label(doc_label)
    if "note" in template:
        raise RuntimeError(f"no extraction schema for document label {doc_label!r}")
    schema_fields = list(template.keys())

    with open(file_ref, "rb") as f:
        raw_bytes = f.read()
    b64_image = base64.b64encode(raw_bytes).decode("ascii")

    prompt = (
        "You are extracting fields from a document image uploaded during a "
        f"bank onboarding flow. The document type is: {doc_label!r}. "
        "Look at the image and extract the following fields as best you "
        f"can: {schema_fields}. "
        "Respond with STRICT JSON only, no prose, matching this schema: "
        f"{json.dumps({field: 'string' for field in schema_fields})}. "
        "If a field is illegible or not present, use an empty string for it."
    )

    logger.info("[doc_parser] attempting real VLM extraction via %s (model=%s)", config.OLLAMA_BASE_URL, model)
    with httpx.Client() as client:
        resp = client.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "images": [b64_image], "format": "json", "stream": False},
            timeout=config.OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        raw_response = resp.json().get("response", "")
        parsed = json.loads(raw_response)

    fields = {field: parsed.get(field, "") for field in schema_fields}
    return {"_mock": False, "_source": "doc_parser.extract_fields (VLM, live)", **fields}


def extract_fields(file_ref: str, doc_label: str) -> dict:
    """Vision-model field extraction for an uploaded document.

    Order of resolution (mirrors classify_document()):
      1. Real attempt at VLM extraction via Ollama (genuine network call;
         expected to fail/timeout in this dev sandbox).
      2. Canned fallback dict, keyed loosely off the document label so
         different document types get plausible-looking (but fake) fields
         -- the path actually exercised in this sandbox.

    Informational/assistive only -- never blocks/fails the upload flow,
    unlike classify_document()."""
    try:
        result = _try_vlm_extract(file_ref, doc_label)
        logger.info("[doc_parser] VLM extraction succeeded: %s", result)
        return result
    except Exception as e:
        logger.info("[doc_parser] VLM extraction unavailable (%s) -- falling back to canned mock fields", e)
        canned = _canned_for_label(doc_label)
        logger.info("[MOCK][doc_parser] extract_fields(%s, %r) -> %s", os.path.basename(file_ref), doc_label, canned)
        return {"_mock": True, "_source": "doc_parser.extract_fields (VLM not available in this build)", **canned}


def _forced_mismatch(original_filename, debug_outcome):
    """Phase 5-style debug hook (see validators.DOC_MISMATCH_FILENAME_MARKER
    and /docs/MOCKS.md): lets demo/test flows deterministically force a
    document-type mismatch instead of depending on the VLM/heuristic
    result, exactly like the existing debug_outcome hook does for the
    document_review scheduled job."""
    if debug_outcome == "reject":
        return True
    if original_filename and validators.DOC_MISMATCH_FILENAME_MARKER in original_filename.lower():
        return True
    return False


def _try_vlm_classify(file_ref: str, expected_doc_label: str) -> dict:
    """Real attempt at VLM-based document-type classification via Ollama.
    This is a genuine network call (base64-encodes the image, hits
    {OLLAMA_BASE_URL}/api/generate with a vision-capable model) using the
    same config wired in backend/config.py -- it is EXPECTED to raise in
    this dev sandbox (no network path to the configured endpoint) and the
    caller (classify_document) falls back to the heuristic check on any
    exception. On a machine with real Ollama connectivity, this is the
    live code path."""
    model = config.OLLAMA_VISION_MODEL or config.OLLAMA_MODEL
    if not model:
        raise RuntimeError("no OLLAMA_VISION_MODEL/OLLAMA_MODEL configured")

    with open(file_ref, "rb") as f:
        raw_bytes = f.read()
    b64_image = base64.b64encode(raw_bytes).decode("ascii")

    prompt = (
        "You are classifying a document image uploaded during a bank "
        f"onboarding flow. The expected document type is: {expected_doc_label!r}. "
        "Look at the image and decide what kind of document it actually is. "
        "Respond with STRICT JSON only, no prose, matching this schema: "
        '{"detected_type": str, "matches_expected": bool, "confidence": float (0-1), "reason": str}.'
    )

    logger.info("[doc_parser] attempting real VLM classification via %s (model=%s)", config.OLLAMA_BASE_URL, model)
    with httpx.Client() as client:
        resp = client.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "images": [b64_image], "format": "json", "stream": False},
            timeout=config.OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        raw_response = resp.json().get("response", "")
        parsed = json.loads(raw_response)

    return {
        "matches_expected": bool(parsed.get("matches_expected", True)),
        "detected_type": str(parsed.get("detected_type", "unknown")),
        "confidence": float(parsed.get("confidence", 0.5)),
        "reason": str(parsed.get("reason", "VLM classification (Ollama)")),
    }


def _heuristic_classify(file_ref: str, expected_doc_label: str) -> dict:
    """Fallback used when the VLM is unreachable/unavailable (the expected
    path in this sandbox). Does something real rather than always-passing:
    - confirms the file is actually readable and non-empty (not a 0-byte
      or corrupt upload)
    - if Pillow can open it, confirms it's a genuine, non-corrupt image
      and records the detected image format
    Always reports low confidence and a clearly-labeled "heuristic only"
    reason -- this is NOT a real document-type classification, just a
    sanity check that *something* plausible was uploaded. Defaults to
    matches_expected=True (so normal demo uploads, including the
    placeholder .txt files used by the existing demo scripts, keep
    passing) unless the file itself is unreadable/empty."""
    try:
        size = os.path.getsize(file_ref)
    except OSError as e:
        logger.warning("[doc_parser] heuristic check: could not stat file %s: %s", file_ref, e)
        return {
            "matches_expected": False,
            "detected_type": "unreadable_file",
            "confidence": 0.6,
            "reason": f"heuristic check only, VLM unavailable -- file could not be read ({e})",
        }

    if size == 0:
        logger.warning("[doc_parser] heuristic check: 0-byte upload for %s", file_ref)
        return {
            "matches_expected": False,
            "detected_type": "empty_file",
            "confidence": 0.6,
            "reason": "heuristic check only, VLM unavailable -- uploaded file is 0 bytes (corrupt/empty upload)",
        }

    is_valid_image = False
    image_format = None
    try:
        from PIL import Image
        with Image.open(file_ref) as img:
            img.verify()
        with Image.open(file_ref) as img2:
            image_format = img2.format
        is_valid_image = True
    except Exception as e:
        logger.info("[doc_parser] heuristic check: %s is not a valid/readable image (%s) -- not treated as a hard failure", file_ref, e)

    ext = os.path.splitext(file_ref)[1].lower()
    if is_valid_image:
        reason = (
            f"heuristic check only, VLM unavailable -- file is a readable {image_format} image "
            f"({size} bytes); could not verify document sub-type without a VLM"
        )
        confidence = 0.3
    else:
        reason = (
            f"heuristic check only, VLM unavailable -- file (ext={ext or 'none'}, {size} bytes) is not a "
            "recognizable image format; cannot verify document type without a VLM, defaulting to pass"
        )
        confidence = 0.15

    return {
        "matches_expected": True,
        "detected_type": expected_doc_label,
        "confidence": confidence,
        "reason": reason,
    }


def classify_document(file_ref: str, expected_doc_label: str, original_filename: str | None = None, debug_outcome: str | None = None) -> dict:
    """Document-type sanity check for an uploaded document.

    Returns: {"matches_expected": bool, "detected_type": str,
              "confidence": float, "reason": str}

    Order of resolution:
      1. Phase-5-style debug hook (debug_outcome=="reject", or the
         uploaded filename carries the DOC_MISMATCH_FILENAME_MARKER) --
         deterministic forced mismatch, for demo/test purposes.
      2. Real attempt at VLM classification via Ollama (genuine network
         call; expected to fail/timeout in this dev sandbox).
      3. Heuristic fallback (file readability / image validity check) --
         the path actually exercised in this sandbox.
    """
    if _forced_mismatch(original_filename, debug_outcome):
        logger.warning(
            "[doc_parser] classify_document: forced mismatch via debug hook (filename=%r, debug_outcome=%r) for expected_doc_label=%r",
            original_filename, debug_outcome, expected_doc_label,
        )
        return {
            "matches_expected": False,
            "detected_type": "deliberately_mismatched_test_document",
            "confidence": 0.95,
            "reason": (
                f"debug hook forced mismatch (debug_outcome={debug_outcome!r}, "
                f"filename_marker_present={bool(original_filename and validators.DOC_MISMATCH_FILENAME_MARKER in original_filename.lower())}); "
                f"expected {expected_doc_label!r}"
            ),
        }

    try:
        result = _try_vlm_classify(file_ref, expected_doc_label)
        logger.info("[doc_parser] VLM classification succeeded: %s", result)
        return result
    except Exception as e:
        logger.info("[doc_parser] VLM classification unavailable (%s) -- falling back to heuristic sanity check", e)
        return _heuristic_classify(file_ref, expected_doc_label)

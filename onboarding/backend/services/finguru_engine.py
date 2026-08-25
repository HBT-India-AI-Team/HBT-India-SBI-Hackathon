"""
FinGuru answer engine -- grounded, cited, India-context financial Q&A.

Reuses the EXISTING Ollama config (backend/config.py) and the onboarding LLM's
model-discovery helper rather than introducing a second LLM config. Grounding
comes from FinGuruTopic rows retrieved by simple keyword/tag matching.

Key design rules (from the FinGuru build prompt, Phase 2):
  * The model must answer ONLY from the retrieved context. If nothing relevant
    is retrieved, we short-circuit to confidence="not_covered" WITHOUT calling
    the LLM at all -- answering ungrounded would undermine the accuracy/coverage
    differentiator and is exactly what the Phase 4 gap-filling flow exists for.
  * Same soft-fail principle as onboarding_llm: if Ollama is unreachable / times
    out / returns invalid JSON, return a graceful "having trouble" response
    (confidence="unavailable") rather than crashing. There is no meaningful
    rule-based fallback for open Q&A, so this is a soft-fail, not a full
    fallback engine.
"""
import json
import logging
import re

import httpx
from pydantic import BaseModel, ValidationError

from backend import config
from backend.models import models as m
# Reuse the onboarding LLM's model auto-discovery instead of duplicating it.
from backend.services.onboarding_llm import _discover_model
# Reuse the onboarding product catalog instead of duplicating product data.
from backend.services import product_catalog

logger = logging.getLogger("yono.finguru")

# Minimum keyword-match score for a topic to count as "relevant" grounding.
# Set high enough that incidental body-word noise (e.g. an off-topic "tech
# merger" question) does NOT spuriously match a topic and skip the not_covered
# path -- a genuine tag/title hit clears this easily.
_RELEVANCE_THRESHOLD = 3.0

_CONFIDENCE_VALUES = {"grounded", "partial", "not_covered"}

# Common words that must not, on their own, make a topic "relevant" -- without
# this, tokens like "the"/"what"/"latest" match nearly every topic body and an
# off-topic question spuriously retrieves grounding (and never reaches the
# not_covered gap path).
_STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "was", "who", "what", "whats", "how",
    "does", "did", "this", "that", "with", "from", "can", "could", "would", "should",
    "will", "about", "into", "when", "where", "which", "why", "have", "has", "had",
    "get", "got", "there", "their", "them", "then", "than", "some", "any", "all",
    "latest", "news", "update", "upcoming", "last", "night", "today", "week", "month",
    "year", "please", "tell", "explain", "know", "want", "need", "much", "many",
    "one", "two", "three", "between", "over", "under", "out", "off", "now", "new",
    "match", "won", "win", "game", "movie", "film", "team", "player",
}


def _words(text: str) -> set:
    import re as _re
    return set(_re.findall(r"[a-z0-9]+", (text or "").lower()))


class _Citation(BaseModel):
    topic_id: str
    label: str


class _EngineLLMOut(BaseModel):
    answer_text: str
    citations: list[_Citation] = []
    follow_up_questions: list[str] = []
    confidence: str = "grounded"


def retrieve_relevant_topics(query: str, db, limit: int = 4) -> list:
    """Simple keyword/tag retrieval over FinGuruTopic.

    NOTE: keyword/tag matching is intentionally simple for the hackathon; a real
    deployment would replace this with embeddings-based semantic retrieval
    (embed the query + topics, rank by cosine similarity). The rest of the
    engine is agnostic to how topics are retrieved, so that swap is localized.
    """
    q = (query or "").lower()
    tokens = {t for t in _words(q) if len(t) >= 3 and t not in _STOPWORDS}
    if not tokens:
        return []
    scored = []
    for t in db.query(m.FinGuruTopic).all():
        score = 0.0
        # Whole-phrase tag match (e.g. multi-word tag appearing verbatim in q).
        for tag in (t.tags or []):
            tag_l = str(tag).lower()
            tag_plain = tag_l.split(":", 1)[-1]  # strip "product_id:" style prefixes
            if " " in tag_plain and tag_plain in q:
                score += 3
        # Word-boundary token matches (NOT substring -- avoids "the" in "there").
        title_w = _words(t.title)
        summary_w = _words(t.summary)
        body_w = _words(t.body)
        tags_w = _words(" ".join(str(x) for x in (t.tags or [])))
        for tok in tokens:
            if tok in title_w:
                score += 2
            if tok in tags_w:
                score += 1.5
            if tok in summary_w:
                score += 1
            if tok in body_w:
                score += 0.3
        if score > 0:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    relevant = [t for score, t in scored if score >= _RELEVANCE_THRESHOLD]
    return relevant[:limit]


# Phase 8: lightweight, documented keyword list for scam-pattern awareness --
# deliberately simple (not a fraud-detection system / ML classifier), per the
# prompt. Matched against the user's OWN message (they're often quoting or
# describing a scheme someone pitched them).
_FRAUD_KEYWORDS = (
    "guaranteed return", "guaranteed returns", "guaranteed profit", "guaranteed income",
    "double your money", "double your investment", "risk-free high return", "risk free high return",
    "assured returns", "assured profit", "100% return", "no risk high return",
    "guaranteed 15%", "weekly returns", "daily returns guaranteed",
)


def _detect_fraud(question_text: str) -> bool:
    q = (question_text or "").lower()
    return any(kw in q for kw in _FRAUD_KEYWORDS)


def _fraud_response() -> dict:
    return {
        "answer_text": "Heads up — schemes promising \"guaranteed high returns\" are a common fraud pattern.",
        "citations": [],
        "follow_up_questions": [],
        "confidence": "grounded",
        "retrieved_topic_ids": [],
        "suggested_widget": None,
        "suggested_action": None,
        "fraud_warning": True,
        "fraud_bullets": [
            "Unrealistic return rates",
            "Pressure to act quickly",
            "Vague company details",
        ],
    }


# ---------------------------------------------------------------------------
# Curated deterministic answer: "what education loans does SBI offer?"
#
# This is the ONE question where the scheme list must come back complete,
# verbatim and in SBI's own order every single time -- an LLM summarising the
# topic body would reorder it and drop entries to stay inside the ~120-word
# answer_text budget in _build_prompt(). So this question short-circuits to the
# curated text below BEFORE the LLM call, exactly like _fraud_response() does.
# The wording mirrors the sbi_education_loans knowledge topic
# (data/finguru_knowledge/education_loans.json), which is what the citation
# chip and the citation view resolve against.
# ---------------------------------------------------------------------------
_EDUCATION_LOANS_TOPIC_ID = "sbi_education_loans"

# The exact list, verbatim and in SBI's published order. Kept as a plain
# triple-quoted literal (no escapes) so what you read here is byte-for-byte
# what the user sees; blank lines and '*' bullets are the light markdown the
# frontend's Markdown component in pages/FinGuruChat.jsx already renders.
_EDUCATION_LOANS_ANSWER = """SBI offers a full range of education loan schemes — here are all of them:

**SBI PM-Vidyalaxmi Scheme**
* For Quality Higher Educational Institutions (QHEIs) selected by Government

**SBI Student Loan Scheme**

**SBI Scholar Loan Scheme**
* For Select Premier Institutions

**SBI Global Ed-Vantage Scheme**
* For Studies abroad (above ₹7.50 lakhs)

**SBI Skill Loan Scheme**
* For pursuing Skill development courses

**Takeover Of Education Loans**

**Dr. Ambedkar Interest Subsidy Scheme for Overseas Studies for OBCs and EBCs**

**Padho Pardesh Interest Subsidy Scheme for Overseas Studies for the Minority Communities**

**Repayment**

**CSIS Scheme**
* INTEREST SUBSIDY SCHEME

**Education Loan MITC**

**Shaurya Education Loan**
* For Defence, Indian Coast Guard & Central Armed Police Force Personnel

**Deceased Borrower/Guarantor**

**Release of Property Documents of EL**"""

_EDUCATION_LOANS_FOLLOW_UPS = [
    "Which one fits studying abroad?",
    "What documents do I need for an education loan?",
    "How does the repayment moratorium work?",
]

# An education word AND a loan word must both appear -- "education" alone would
# also match e.g. the Sukanya Samriddhi topic ("for a girl child's education").
_EDU_WORDS = ("education", "educational", "student", "study", "studies", "college", "university", "tuition")
_LOAN_WORDS = ("loan", "loans", "borrow", "finance", "financing", "funding")
# A named scheme is specific enough to match on its own.
_EDU_SCHEME_NAMES = (
    "vidyalaxmi", "vidya laxmi", "ed-vantage", "ed vantage", "edvantage",
    "scholar loan", "skill loan", "shaurya", "padho pardesh", "csis",
    "ambedkar interest subsidy",
)


def _detect_education_loan_query(question_text: str) -> bool:
    q = (question_text or "").lower()
    if any(name in q for name in _EDU_SCHEME_NAMES):
        return True
    return any(w in q for w in _EDU_WORDS) and any(w in q for w in _LOAN_WORDS)


def _education_loans_response(db) -> dict:
    """Curated list response. Cites the real sbi_education_loans topic when it
    has been seeded (python -m backend.scripts.seed_finguru_knowledge); if the
    KB has not been seeded the answer is still returned, just uncited, rather
    than pointing the citation view at a topic id that does not exist."""
    cited = []
    try:
        if db.query(m.FinGuruTopic).filter_by(id=_EDUCATION_LOANS_TOPIC_ID).first():
            cited = [{"topic_id": _EDUCATION_LOANS_TOPIC_ID, "label": "SBI Education Loan Schemes"}]
    except Exception as e:  # DB hiccup must not lose the answer itself
        logger.warning("[finguru] education-loan citation lookup failed: %s: %s", type(e).__name__, e)
    return {
        "answer_text": _EDUCATION_LOANS_ANSWER,
        "citations": cited,
        "follow_up_questions": list(_EDUCATION_LOANS_FOLLOW_UPS),
        "confidence": "grounded",
        "retrieved_topic_ids": [c["topic_id"] for c in cited],
        "suggested_widget": None,
        "suggested_action": None,
        "suggested_product_id": None,
        "fraud_warning": False,
        "fraud_bullets": [],
    }

def _detect_product_handoff(citations: list, id_to_topic: dict) -> str | None:
    """Phase 8: if any cited topic maps to a real onboarding product (via its
    "product_id:<id>" tag), validate that id against the SAME product catalog
    onboarding uses (backend/services/product_catalog.py) rather than trusting
    the tag blindly or duplicating product data."""
    for c in citations:
        topic = id_to_topic.get(c.get("topic_id"))
        if not topic:
            continue
        for tag in (topic.tags or []):
            tag_s = str(tag)
            if tag_s.startswith("product_id:"):
                pid = tag_s.split(":", 1)[1]
                try:
                    product_catalog.get_product(pid)
                    return pid
                except KeyError:
                    continue
    return None


def _detect_suggested_widget(question_text: str) -> str | None:
    """Phase 6: lightweight keyword detection for calculator-worthy intent.
    Pure heuristic (not ML) -- the frontend renders the matching widget card
    alongside the text answer when this is non-null."""
    q = (question_text or "").lower()
    if "sip" in q and any(kw in q for kw in ("calculat", "grow", "how much", "future value", "maturity")):
        return "sip_calculator"
    return None


def _not_covered_response(question_text: str) -> dict:
    """Standard 'we don't have grounded info on this' response that the Phase 4
    gap-filling flow keys off. Deliberately does NOT answer from general
    knowledge."""
    return {
        "answer_text": (
            "I don't have solid, verified info on this yet — I only answer from "
            "sources I can cite. Want me to look into it and get back to you?"
        ),
        "citations": [],
        "follow_up_questions": [],
        "confidence": "not_covered",
        "retrieved_topic_ids": [],
    }


def _soft_fail_response() -> dict:
    """Transient service error (Ollama down / bad JSON). Distinct from
    not_covered so the frontend does NOT offer to 'research' a question that
    actually just hit a temporary outage."""
    return {
        "answer_text": "I'm having trouble reaching my knowledge engine right now. Please try again in a moment.",
        "citations": [],
        "follow_up_questions": [],
        "confidence": "unavailable",
        "retrieved_topic_ids": [],
    }


def _build_prompt(topics: list, history: list, question_text: str) -> str:
    context_blocks = []
    for t in topics:
        context_blocks.append(
            {
                "topic_id": t.id,
                "title": t.title,
                "category": t.category,
                "content": (t.body or t.summary or ""),
                "last_verified_at": t.last_verified_at.strftime("%b %Y") if t.last_verified_at else None,
            }
        )
    recent = [
        {"direction": h.get("direction"), "text": (h.get("content") or {}).get("text")}
        for h in (history or [])[-6:]
    ]
    return (
        "You are FinGuru, an India-context financial education assistant inside the SBI YONO app. "
        "You must reply with STRICT JSON only, matching this schema: "
        '{"answer_text": str, "citations": [{"topic_id": str, "label": str}], '
        '"follow_up_questions": [str], "confidence": "grounded" | "partial" | "not_covered"}. '
        "No prose outside the JSON.\n\n"
        "RULES:\n"
        "1. Answer ONLY using the provided context topics below. Do NOT use outside knowledge.\n"
        "2. If the context does not actually contain the answer, set confidence to \"not_covered\" "
        "and keep answer_text to a brief honest sentence (do not guess).\n"
        "3. In citations, list the topic_id(s) you actually used, each with a short human label "
        "(e.g. the topic title, or a phrase like \"Per RBI guidelines\").\n"
        "4. Propose 2-3 short, relevant follow_up_questions the user might ask next.\n"
        "5. Use Indian context and rupees (₹). Keep answer_text concise and friendly "
        "(about 120 words max) so the JSON is never truncated.\n"
        "6. confidence: \"grounded\" if fully answered from context, \"partial\" if only partly.\n\n"
        f"Context topics (JSON): {json.dumps(context_blocks, ensure_ascii=False)}\n\n"
        f"Recent conversation: {json.dumps(recent, ensure_ascii=False)}\n\n"
        f"User question: {question_text}"
    )


def _extract_json_object(raw: str) -> str:
    """Return the first balanced top-level {...} object from raw text.

    The Ollama endpoint (via ngrok) occasionally appends trailing text after the
    JSON object ("Extra data" JSONDecodeError). Scanning for the first balanced
    object -- respecting string literals/escapes -- recovers the valid object in
    that case. Raises ValueError if no balanced object is present (e.g. the
    output was truncated mid-string), which the caller treats as a retryable
    failure."""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
    raise ValueError("unbalanced/truncated JSON object")


def _call_llm(topics: list, history: list, question_text: str) -> _EngineLLMOut:
    with httpx.Client() as client:
        model = _discover_model(client)
        if not model:
            raise RuntimeError("no Ollama model available")
        prompt = _build_prompt(topics, history, question_text)
        logger.debug("[finguru] calling %s/api/generate model=%s", config.OLLAMA_BASE_URL, model)
        resp = client.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                # num_predict prevents the JSON being truncated mid-string; a
                # low default caps output and produces "Unterminated string".
                "options": {"num_predict": config.FINGURU_LLM_NUM_PREDICT, "temperature": 0.2},
            },
            timeout=config.FINGURU_LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        try:
            parsed_json = json.loads(raw)
        except json.JSONDecodeError:
            # Recover from trailing "Extra data" by taking the first balanced
            # object; a truncated object re-raises and triggers a retry upstream.
            parsed_json = json.loads(_extract_json_object(raw))
        return _EngineLLMOut.model_validate(parsed_json)


def ask_generic(question_text: str) -> dict:
    """Phase 7 (comparison mode): what a plain, UNGROUNDED assistant would say
    -- the SAME Ollama model, but with no retrieved-topic context and no
    instruction to cite sources. This exists only so the comparison view shows
    two genuinely different answers, not two calls down the grounded path."""
    try:
        with httpx.Client() as client:
            model = _discover_model(client)
            if not model:
                raise RuntimeError("no Ollama model available")
            prompt = f"Answer this financial question: {question_text}"
            resp = client.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model, "prompt": prompt, "stream": False,
                    "options": {"num_predict": config.FINGURU_LLM_NUM_PREDICT, "temperature": 0.7},
                },
                timeout=config.FINGURU_LLM_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            text = (resp.json().get("response") or "").strip()
            return {"answer_text": text or "(no response)"}
    except Exception as e:
        logger.warning("[finguru] generic (ungrounded) compare call failed: %s: %s", type(e).__name__, e)
        return {"answer_text": "I'm having trouble generating a comparison answer right now."}


def ask(conversation, question_text: str, db, history: list | None = None) -> dict:
    """Returns a structured dict:
    {answer_text, citations:[{topic_id,label}], follow_up_questions:[str],
     confidence, retrieved_topic_ids:[str]}.
    Never raises -- soft-fails to a graceful message on any LLM error.
    """
    # Phase 8 fraud/scam awareness check FIRST -- fast, deterministic, no LLM
    # call needed. Checked on the user's own message (they're often describing
    # a pitch someone sent them).
    if _detect_fraud(question_text):
        logger.info("[finguru] fraud-pattern keywords matched for %r", question_text)
        return _fraud_response()

    # Curated deterministic answer for the SBI education-loan scheme list --
    # also before the LLM call, for the reasons documented above.
    if _detect_education_loan_query(question_text):
        logger.info("[finguru] education-loan query matched for %r -> curated list", question_text)
        return _education_loans_response(db)

    # Context-aware retrieval: a follow-up like "who is eligible to open one?"
    # has no topic keywords on its own, so anchor it with the most recent user
    # question in this conversation (simple keyword retrieval has no memory).
    retrieval_query = question_text
    for h in reversed(history or []):
        if h.get("direction") == "inbound":
            prev = (h.get("content") or {}).get("text")
            if prev and prev.strip() != question_text.strip():
                retrieval_query = f"{prev} {question_text}"
            break
    topics = retrieve_relevant_topics(retrieval_query, db)
    retrieved_ids = [t.id for t in topics]

    # No grounding found -> gap-filling path, do NOT call the LLM ungrounded.
    if not topics:
        logger.info("[finguru] no relevant topics for %r -> not_covered", question_text)
        return _not_covered_response(question_text)

    # The ngrok Ollama endpoint intermittently truncates JSON / drops the
    # connection; a single retry recovers most of those transient failures
    # before we soft-fail.
    parsed = None
    last_err = None
    for attempt in (1, 2):
        try:
            parsed = _call_llm(topics, history, question_text)
            break
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, ValidationError, RuntimeError, KeyError) as e:
            last_err = e
            logger.warning("[finguru] LLM attempt %d failed (%s: %s)", attempt, type(e).__name__, e)
    if parsed is None:
        logger.warning("[finguru] soft-fail after retries (%s)", last_err)
        return _soft_fail_response()

    # Validate citations reference topics we actually retrieved (no hallucinated ids).
    valid_ids = {t.id for t in topics}
    id_to_topic = {t.id: t for t in topics}
    citations = []
    for c in parsed.citations:
        if c.topic_id in valid_ids:
            label = c.label or id_to_topic[c.topic_id].title
            citations.append({"topic_id": c.topic_id, "label": label})

    confidence = parsed.confidence if parsed.confidence in _CONFIDENCE_VALUES else "partial"
    # Model itself judged the context insufficient -> treat as a gap.
    if confidence == "not_covered":
        logger.info("[finguru] model returned not_covered for %r", question_text)
        return _not_covered_response(question_text)

    product_id = _detect_product_handoff(citations, id_to_topic)

    return {
        "answer_text": parsed.answer_text,
        "citations": citations,
        "follow_up_questions": parsed.follow_up_questions[:3],
        "confidence": confidence,
        "retrieved_topic_ids": retrieved_ids,
        "suggested_widget": _detect_suggested_widget(question_text),
        "suggested_action": "start_onboarding" if product_id else None,
        "suggested_product_id": product_id,
        "fraud_warning": False,
        "fraud_bullets": [],
    }

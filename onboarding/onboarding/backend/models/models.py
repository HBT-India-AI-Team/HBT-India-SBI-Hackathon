"""
SQLAlchemy models for YONO 3.0 backend redesign.

See /backend/YONO_3.0_Backend_Redesign_BuildPrompts.md Phase 1 for the spec
this file implements. Application.get_progress() / status are NEVER stored
independently -- they are always derived from Requirement state via
services/requirement_graph.py (imported lazily inside methods to avoid a
circular import between models and services).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.models.db import Base


def gen_id() -> str:
    return uuid.uuid4().hex


class ApplicationStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTION_NEEDED = "ACTION_NEEDED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ABANDONED = "ABANDONED"


class RequirementState(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    AWAITING_INPUT = "AWAITING_INPUT"
    SUBMITTED = "SUBMITTED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    mobile_number = Column(String, unique=True, nullable=True, index=True)
    pan_masked = Column(String, nullable=True)
    language = Column(String, default="en")
    telegram_chat_id = Column(String, nullable=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    applications = relationship("Application", back_populates="user")


class Application(Base):
    __tablename__ = "applications"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    product_id = Column(String, nullable=False)
    status = Column(String, default=ApplicationStatus.IN_PROGRESS.value)
    channel_origin = Column(String, default="web")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="applications")
    requirements = relationship("Requirement", back_populates="application", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="application", cascade="all, delete-orphan")

    def get_progress(self):
        from backend.services import requirement_graph
        return requirement_graph.compute_progress(self)

    def get_status(self):
        from backend.services import requirement_graph
        return requirement_graph.compute_application_status(self)


class Requirement(Base):
    __tablename__ = "requirements"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    type = Column(String, nullable=False)
    label = Column(String, nullable=False)
    format_hint = Column(String, nullable=True)
    mapped_step = Column(Integer, default=1)
    state = Column(String, default=RequirementState.NOT_STARTED.value)
    failure_count = Column(Integer, default=0)
    value = Column(String, nullable=True)
    scope = Column(String, nullable=True)  # e.g. "guardian" for guardian-only requirements
    depends_on = Column(JSON, default=list)
    escalation_threshold = Column(Integer, default=2)
    otp_code_hash = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    application = relationship("Application", back_populates="requirements")
    documents = relationship("DocumentSubmission", back_populates="requirement", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    channel = Column(String, default="web")
    scope = Column(String, nullable=True)  # null = normal session; "guardian" = guardian-scoped
    started_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="active")

    application = relationship("Application", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    direction = Column(String, nullable=False)  # inbound/outbound
    content_type = Column(String, default="text")
    content_payload = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="messages")


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    purpose = Column(String, nullable=False)
    granted = Column(Boolean, default=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class DocumentSubmission(Base):
    __tablename__ = "document_submissions"
    id = Column(String, primary_key=True, default=gen_id)
    requirement_id = Column(String, ForeignKey("requirements.id"), nullable=False)
    file_ref = Column(String, nullable=False)
    status = Column(String, default="SUBMITTED")
    submitted_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)
    extracted_fields = Column(JSON, nullable=True)
    debug_outcome = Column(String, nullable=True)  # "verify" | "reject" | None
    classification = Column(JSON, nullable=True)  # result of doc_parser.classify_document()
    rejection_reason = Column(String, nullable=True)  # e.g. "document_type_mismatch: ..."

    requirement = relationship("Requirement", back_populates="documents")


class ReviewItem(Base):
    __tablename__ = "review_items"
    id = Column(String, primary_key=True, default=gen_id)
    # nullable: FinGuru content_research items (Phase 4) can be raised from a
    # FinGuru conversation that has no onboarding Application attached.
    application_id = Column(String, ForeignKey("applications.id"), nullable=True)
    type = Column(String, nullable=False)  # kyc_review | support_request | content_research
    reason = Column(String, nullable=True)
    status = Column(String, default="open")  # open/resolved
    decision = Column(String, nullable=True)
    note = Column(String, nullable=True)
    requirement_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=True)
    job_type = Column(String, nullable=False)  # document_review | idle_nudge_check
    scheduled_for = Column(DateTime, nullable=False)
    status = Column(String, default="pending")  # pending/done/failed
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id = Column(String, primary_key=True, default=gen_id)
    # nullable: FinGuru research-answered notifications (Phase 4) reuse this same
    # log but may not be tied to an onboarding Application.
    application_id = Column(String, ForeignKey("applications.id"), nullable=True)
    channel = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    mock_sent = Column(Boolean, default=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=True)
    type = Column(String, default="chat")  # chat/callback
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class GuardianInfo(Base):
    __tablename__ = "guardian_info"
    id = Column(String, primary_key=True, default=gen_id)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    mobile_number = Column(String, nullable=True)
    relationship = Column(String, nullable=True)
    proof_doc_ref = Column(String, nullable=True)
    otp_verified = Column(Boolean, default=False)
    session_id = Column(String, nullable=True)


class DataRightsRequest(Base):
    __tablename__ = "data_rights_requests"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    request_type = Column(String, nullable=False)  # access/deletion
    status = Column(String, default="pending_manual_review")
    created_at = Column(DateTime, default=datetime.utcnow)
    fulfilled_at = Column(DateTime, nullable=True)


class HandoffToken(Base):
    """Generic short-lived single-use token: channel handoff (Phase 10) and
    guardian session links (Phase 9) both reuse this same table."""
    __tablename__ = "handoff_tokens"
    id = Column(String, primary_key=True, default=gen_id)
    token = Column(String, unique=True, nullable=False, index=True)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False)
    target_channel = Column(String, nullable=False)  # web/whatsapp/telegram/guardian
    purpose = Column(String, default="handoff")  # handoff | guardian_link
    extra = Column(JSON, default=dict)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


# ---------------------------------------------------------------------------
# FinGuru -- India-context financial Q&A assistant (added by the FinGuru build).
# A distinct feature that SHARES infrastructure with onboarding (Ollama config,
# product catalog, HITL ReviewItem queue, NotificationLog, STT pipeline) rather
# than duplicating it. These are its own persistence rows.
# ---------------------------------------------------------------------------
class FinGuruTopic(Base):
    """A single grounded knowledge entry FinGuru can cite. Seeded via
    backend/scripts/seed_finguru_knowledge.py (Phase 2)."""
    __tablename__ = "finguru_topics"
    id = Column(String, primary_key=True, default=gen_id)
    category = Column(String, nullable=False)  # fin_wiki | product | govt_scheme
    title = Column(String, nullable=False)
    tags = Column(JSON, default=list)
    summary = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    # Phase 6 (schemes explorer): eligibility chips for govt_scheme entries.
    eligibility_tags = Column(JSON, default=list)
    # Phase 5 (trending): incremented each time this topic is cited in an answer.
    query_count = Column(Integer, default=0)
    # Same "populated via web research, needs stakeholder review" disclaimer
    # pattern as product_requirements.json.
    needs_review = Column(Boolean, default=True)
    last_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinGuruConversation(Base):
    __tablename__ = "finguru_conversations"
    id = Column(String, primary_key=True, default=gen_id)
    # nullable: FinGuru can be used before/without onboarding (no User yet).
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("FinGuruMessage", back_populates="conversation", cascade="all, delete-orphan")


class FinGuruMessage(Base):
    __tablename__ = "finguru_messages"
    id = Column(String, primary_key=True, default=gen_id)
    conversation_id = Column(String, ForeignKey("finguru_conversations.id"), nullable=False)
    direction = Column(String, nullable=False)  # inbound | outbound
    content_type = Column(String, default="text")  # text | audio
    content_payload = Column(JSON, default=dict)
    citations = Column(JSON, nullable=True)  # [{topic_id, label}]
    follow_up_suggestions = Column(JSON, nullable=True)  # [str]
    timestamp = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("FinGuruConversation", back_populates="messages")


class ResearchRequest(Base):
    """Gap-filling request (Phase 4). Links to the shared ReviewItem HITL queue
    via review_item_id rather than introducing a parallel queue."""
    __tablename__ = "research_requests"
    id = Column(String, primary_key=True, default=gen_id)
    conversation_id = Column(String, ForeignKey("finguru_conversations.id"), nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    question_text = Column(Text, nullable=False)
    status = Column(String, default="queued")  # queued | researching | answered
    review_item_id = Column(String, ForeignKey("review_items.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    answered_at = Column(DateTime, nullable=True)

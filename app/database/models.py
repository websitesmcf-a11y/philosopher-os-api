import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, Boolean, DateTime, Date, ForeignKey, JSON, Enum as SAEnum, Index, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


# ─── Portable column types (PostgreSQL + SQLite) ───────────────────────

from sqlalchemy import types as satypes


class GUID(satypes.TypeDecorator):
    """Portable UUID — native on PostgreSQL, CHAR(32) on SQLite.

    Accepts both uuid.UUID objects and UUID strings as bind parameters.
    """
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, str) and value:
            return uuid.UUID(value)
        return value


def UUID(as_uuid: bool = True) -> "GUID":
    return GUID()


# Arrays are native on PostgreSQL; stored as JSON on SQLite
StringArray = ARRAY(String).with_variant(JSON(), "sqlite")

# Embeddings: pgvector on PostgreSQL when available, JSON elsewhere
try:
    from pgvector.sqlalchemy import Vector
    EmbeddingType = Vector(1536).with_variant(JSON(), "sqlite")
except ImportError:
    EmbeddingType = JSON


# ─── Enums ──────────────────────────────────────────────────────────────

class LeadStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"

class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


# ─── Mixins ─────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ─── Core Tables ────────────────────────────────────────────────────────

class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    settings = Column(JSON, default=dict)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clerk_id = Column(String(255), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    avatar_url = Column(String(1024))
    role = Column(String(50), default="member")
    preferences = Column(JSON, default=dict)


class OrgMember(Base):
    __tablename__ = "org_members"

    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(50), default="member")
    permissions = Column(StringArray, default=list)
    joined_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# ─── CRM ────────────────────────────────────────────────────────────────

class Lead(Base, TimestampMixin):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(50))
    email = Column(String(255))
    company = Column(String(255))
    industry = Column(String(255))
    source = Column(String(100))
    status = Column(String(50), default="new")
    score = Column(Integer, default=0)
    tags = Column(StringArray, default=list)
    notes = Column(Text)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_by = Column(String(255), nullable=True)
    first_contacted_at = Column(DateTime(timezone=True))
    last_contacted_at = Column(DateTime(timezone=True))
    converted_at = Column(DateTime(timezone=True))
    custom_fields = Column(JSON, default=dict)
    list_id = Column(UUID(as_uuid=True), nullable=True)  # lead list membership
    reservation_id = Column(String(100), nullable=True)  # campaign that reserved this lead
    embedding = Column("embedding", EmbeddingType, nullable=True)


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"))
    name = Column(String(255), nullable=False)
    phone = Column(String(50))
    email = Column(String(255))
    company = Column(String(255))
    industry = Column(String(255))
    contract_status = Column(String(50), default="active")
    mrr = Column(Float, default=0.0)
    lifetime_value = Column(Float, default=0.0)
    embedding = Column("embedding", EmbeddingType, nullable=True)


# ─── Messages ───────────────────────────────────────────────────────────

class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"))
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"))
    channel = Column(String(50), nullable=False)
    status = Column(String(50), default="active")
    extra_metadata = Column("metadata", JSON, default=dict)
    last_message_at = Column(DateTime(timezone=True))


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    sender_type = Column(String(50), nullable=False)
    sender_id = Column(String(255))
    direction = Column(String(10), nullable=False)
    body = Column(Text, nullable=False)
    media_url = Column(StringArray, default=list)
    extra_metadata = Column("metadata", JSON, default=dict)
    embedding = Column("embedding", EmbeddingType, nullable=True)


# ─── Finance ────────────────────────────────────────────────────────────

class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"))
    invoice_number = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD")
    status = Column(String(50), default="draft")
    due_date = Column(Date)
    paid_at = Column(DateTime(timezone=True))
    lines = Column(JSON, default=list)


class Expense(Base, TimestampMixin):
    __tablename__ = "expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD")
    description = Column(Text)
    receipt_url = Column(String(1024))
    incurred_at = Column(Date, nullable=False)


class RevenueEvent(Base, TimestampMixin):
    __tablename__ = "revenue_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"))
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"))
    amount = Column(Float, nullable=False)
    type = Column(String(50), nullable=False)
    period_start = Column(Date)
    period_end = Column(Date)


# ─── Campaigns ──────────────────────────────────────────────────────────

class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    channel = Column(String(50), nullable=False)
    industry = Column(String(255))
    message_template = Column(Text, nullable=False)
    status = Column(String(50), default="draft")
    schedule_config = Column(JSON, default=dict)
    target_count = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    conversion_count = Column(Integer, default=0)
    lead_list_id = Column(UUID(as_uuid=True), nullable=True)
    extra_data = Column(JSON, default=dict)
    owner_id = Column(UUID(as_uuid=True), nullable=True)


class CampaignLead(Base):
    __tablename__ = "campaign_leads"

    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    status = Column(String(50), default="pending")
    sent_at = Column(DateTime(timezone=True))
    replied_at = Column(DateTime(timezone=True))


# ─── Memory & Knowledge ────────────────────────────────────────────────

class AgentMemory(Base, TimestampMixin):
    __tablename__ = "agent_memory"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(String(100), nullable=False)
    memory_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    extra_metadata = Column("metadata", JSON, default=dict)
    importance = Column(Float, default=0.5)
    accessed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    embedding = Column("embedding", EmbeddingType, nullable=True)


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_base"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(255))
    tags = Column(StringArray, default=list)
    embedding = Column("embedding", EmbeddingType, nullable=True)


# ─── Tasks & Calendar ──────────────────────────────────────────────────

class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    assignee_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    assigned_agent = Column(String(100))
    priority = Column(String(50), default="medium")
    status = Column(String(50), default="pending")
    due_date = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    related_to_type = Column(String(50))
    related_to_id = Column(String(255))


class CalendarEvent(Base, TimestampMixin):
    __tablename__ = "calendar_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    event_type = Column(String(50), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    attendees = Column(JSON, default=list)
    location = Column(String(500))
    meeting_link = Column(String(1024))
    status = Column(String(50), default="scheduled")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    # External-calendar linkage (e.g. Google event id) for two-way sync dedupe
    external_id = Column(String(255), index=True)


# ─── Automation ─────────────────────────────────────────────────────────

class AutomationRule(Base, TimestampMixin):
    __tablename__ = "automation_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    trigger_event = Column(String(255), nullable=False)
    conditions = Column(JSON, default=dict)
    actions = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime(timezone=True))


class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    job_type = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(50), default="pending")
    result = Column(JSON)
    error = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)


# ─── Audit & Notifications ─────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    action = Column(String(255), nullable=False)
    resource_type = Column(String(255), nullable=False)
    resource_id = Column(String(255))
    details = Column(JSON, default=dict)
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text)
    data = Column(JSON, default=dict)
    read = Column(Boolean, default=False)


# ─── Hermes Background Jobs ────────────────────────────────────────────

class HermesJob(Base, TimestampMixin):
    """Persistent background job record. Survives server restarts."""
    __tablename__ = "hermes_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    parent_job_id = Column(UUID(as_uuid=True), ForeignKey("hermes_jobs.id", ondelete="SET NULL"), nullable=True)
    agent_name = Column(String(100), nullable=False)
    task = Column(Text, nullable=False)
    task_type = Column(String(100), default="general")
    source = Column(String(100), default="api")       # api | beast_mode | campaign | schedule | chain
    mission_id = Column(String(255), nullable=True)   # links to beast mode missions
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, nullable=True)
    status = Column(String(50), default="queued")     # queued|running|completed|failed|cancelled|retrying
    progress_percent = Column(Integer, default=0)
    progress_message = Column(Text, nullable=True)
    current_step = Column(String(255), nullable=True)
    completed_steps = Column(Integer, default=0)
    total_steps = Column(Integer, nullable=True)
    priority = Column(Integer, default=5)
    max_attempts = Column(Integer, default=2)
    attempt_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    cancellation_requested = Column(Boolean, default=False)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)


class HermesJobLog(Base):
    """Structured log entries for a Hermes job."""
    __tablename__ = "hermes_job_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("hermes_jobs.id", ondelete="CASCADE"), nullable=False)
    org_id = Column(UUID(as_uuid=True), nullable=True)
    level = Column(String(20), default="info")        # debug | info | warning | error | success
    message = Column(Text, nullable=False)
    extra_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


# ─── Integrations / Connections ────────────────────────────────────────

class Integration(Base, TimestampMixin):
    """A connected external service (WhatsApp, Facebook, email, LLM keys, ...).

    Credentials are stored encrypted (Fernet) in `credentials_enc`; non-secret
    settings (URLs, page IDs, sender addresses) live in plain `config` JSON.
    """
    __tablename__ = "integrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    provider = Column(String(100), nullable=False, unique=True)
    status = Column(String(50), default="disconnected")  # disconnected | connected | error
    config = Column(JSON, default=dict)
    credentials_enc = Column(Text)
    last_checked_at = Column(DateTime(timezone=True))
    last_error = Column(Text)

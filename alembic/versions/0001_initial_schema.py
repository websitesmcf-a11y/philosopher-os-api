"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-06-11 08:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSON

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── Organizations ──────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("settings", JSON, default=dict),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Users ───────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("clerk_id", sa.String(255), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.String(1024)),
        sa.Column("role", sa.String(50), default="member"),
        sa.Column("preferences", JSON, default=dict),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Org Members ─────────────────────────────────────
    op.create_table(
        "org_members",
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(50), default="member"),
        sa.Column("permissions", ARRAY(sa.String), default=list),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
    )

    # ── Leads ───────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("company", sa.String(255)),
        sa.Column("industry", sa.String(255)),
        sa.Column("source", sa.String(100)),
        sa.Column("status", sa.String(50), default="new"),
        sa.Column("score", sa.Integer, default=0),
        sa.Column("tags", ARRAY(sa.String), default=list),
        sa.Column("notes", sa.Text),
        sa.Column("assigned_to", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("first_contacted_at", sa.DateTime(timezone=True)),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True)),
        sa.Column("converted_at", sa.DateTime(timezone=True)),
        sa.Column("custom_fields", JSON, default=dict),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_leads_org_id", "leads", ["org_id"])
    op.create_index("ix_leads_status", "leads", ["status"])

    # ── Clients ─────────────────────────────────────────
    op.create_table(
        "clients",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("company", sa.String(255)),
        sa.Column("industry", sa.String(255)),
        sa.Column("contract_status", sa.String(50), default="active"),
        sa.Column("mrr", sa.Float, default=0.0),
        sa.Column("lifetime_value", sa.Float, default=0.0),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_clients_org_id", "clients", ["org_id"])

    # ── Conversations ───────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id")),
        sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("clients.id")),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), default="active"),
        sa.Column("metadata", JSON, default=dict),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_org_id", "conversations", ["org_id"])

    # ── Messages ────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_type", sa.String(50), nullable=False),
        sa.Column("sender_id", sa.String(255)),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("media_url", ARRAY(sa.String), default=list),
        sa.Column("metadata", JSON, default=dict),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ── Invoices ─────────────────────────────────────────
    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("clients.id")),
        sa.Column("invoice_number", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(10), default="USD"),
        sa.Column("status", sa.String(50), default="draft"),
        sa.Column("due_date", sa.Date),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("lines", JSON, default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoices_org_id", "invoices", ["org_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])

    # ── Expenses ─────────────────────────────────────────
    op.create_table(
        "expenses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(10), default="USD"),
        sa.Column("description", sa.Text),
        sa.Column("receipt_url", sa.String(1024)),
        sa.Column("incurred_at", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Revenue Events ───────────────────────────────────
    op.create_table(
        "revenue_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("clients.id")),
        sa.Column("invoice_id", UUID(as_uuid=True), sa.ForeignKey("invoices.id")),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── Campaigns ────────────────────────────────────────
    op.create_table(
        "campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("industry", sa.String(255)),
        sa.Column("message_template", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), default="draft"),
        sa.Column("schedule_config", JSON, default=dict),
        sa.Column("target_count", sa.Integer, default=0),
        sa.Column("sent_count", sa.Integer, default=0),
        sa.Column("reply_count", sa.Integer, default=0),
        sa.Column("conversion_count", sa.Integer, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaigns_org_id", "campaigns", ["org_id"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    # ── Campaign Leads ───────────────────────────────────
    op.create_table(
        "campaign_leads",
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
    )

    # ── Agent Memory ─────────────────────────────────────
    op.create_table(
        "agent_memory",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", JSON, default=dict),
        sa.Column("importance", sa.Float, default=0.5),
        sa.Column("accessed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_memory_agent", "agent_memory", ["agent_name"])
    op.create_index("ix_agent_memory_org_id", "agent_memory", ["org_id"])

    # ── Knowledge Base ──────────────────────────────────
    op.create_table(
        "knowledge_base",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("category", sa.String(255)),
        sa.Column("tags", ARRAY(sa.String), default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_base_org_id", "knowledge_base", ["org_id"])

    # ── Tasks ────────────────────────────────────────────
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("assignee_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("assigned_agent", sa.String(100)),
        sa.Column("priority", sa.String(50), default="medium"),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("due_date", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("related_to_type", sa.String(50)),
        sa.Column("related_to_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_org_id", "tasks", ["org_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # ── Calendar Events ──────────────────────────────────
    op.create_table(
        "calendar_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attendees", JSON, default=list),
        sa.Column("location", sa.String(500)),
        sa.Column("meeting_link", sa.String(1024)),
        sa.Column("status", sa.String(50), default="scheduled"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_events_org_id", "calendar_events", ["org_id"])

    # ── Automation Rules ─────────────────────────────────
    op.create_table(
        "automation_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("trigger_event", sa.String(255), nullable=False),
        sa.Column("conditions", JSON, default=dict),
        sa.Column("actions", JSON, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_rules_org_id", "automation_rules", ["org_id"])

    # ── Scheduled Jobs ──────────────────────────────────
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id")),
        sa.Column("job_type", sa.String(255), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("result", JSON),
        sa.Column("error", sa.Text),
        sa.Column("retry_count", sa.Integer, default=0),
        sa.Column("max_retries", sa.Integer, default=3),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scheduled_jobs_org_id", "scheduled_jobs", ["org_id"])
    op.create_index("ix_scheduled_jobs_status", "scheduled_jobs", ["status"])

    # ── Audit Logs ──────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("resource_type", sa.String(255), nullable=False),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("details", JSON, default=dict),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    # ── Notifications ────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("data", JSON, default=dict),
        sa.Column("read", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_read", "notifications", ["read"])

    # ── pgvector columns (manually added — autogenerate skips type_=None) ─
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS embedding vector(1536)")

    # ── IVFFlat indexes for vector columns ──────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_embedding ON leads "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clients_embedding ON clients "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_embedding ON messages "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memory_embedding ON agent_memory "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_base_embedding ON knowledge_base "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    # Drop pgvector indexes
    op.execute("DROP INDEX IF EXISTS ix_knowledge_base_embedding")
    op.execute("DROP INDEX IF EXISTS ix_agent_memory_embedding")
    op.execute("DROP INDEX IF EXISTS ix_messages_embedding")
    op.execute("DROP INDEX IF EXISTS ix_clients_embedding")
    op.execute("DROP INDEX IF EXISTS ix_leads_embedding")

    # Drop pgvector columns
    op.execute("ALTER TABLE knowledge_base DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS embedding")

    # Drop tables (reverse dependency order)
    op.drop_table("notifications")
    op.drop_table("audit_logs")
    op.drop_table("scheduled_jobs")
    op.drop_table("automation_rules")
    op.drop_table("calendar_events")
    op.drop_table("tasks")
    op.drop_table("knowledge_base")
    op.drop_table("agent_memory")
    op.drop_table("campaign_leads")
    op.drop_table("campaigns")
    op.drop_table("revenue_events")
    op.drop_table("expenses")
    op.drop_table("invoices")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("clients")
    op.drop_table("leads")
    op.drop_table("org_members")
    op.drop_table("users")
    op.drop_table("organizations")

"""Add calendar_events.external_id for external calendar sync (Google).

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("external_id", sa.String(255), nullable=True))
    op.create_index("ix_calendar_events_external_id", "calendar_events", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_external_id", table_name="calendar_events")
    op.drop_column("calendar_events", "external_id")

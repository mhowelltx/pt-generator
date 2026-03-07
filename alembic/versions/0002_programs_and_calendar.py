"""Add programs, program_sessions tables and session_date index

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index for calendar date-range queries (Phase 2)
    op.create_index("ix_sessions_session_date", "sessions", ["session_date"])

    # Programs table (Phase 3)
    op.create_table(
        "programs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("user_id", sa.Text, sa.ForeignKey("trainers.user_id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("goal_focus", sa.Text, nullable=True),
        sa.Column("start_date", sa.Text, nullable=True),
        sa.Column("end_date", sa.Text, nullable=True),
        sa.Column("weeks", sa.Integer, nullable=False, server_default="4"),
        sa.Column("sessions_per_week", sa.Integer, nullable=False, server_default="3"),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'draft'")),
        sa.Column("program_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_programs_client_id", "programs", ["client_id"])
    op.create_index("ix_programs_user_id", "programs", ["user_id"])

    # Program sessions (slots) table (Phase 3)
    op.create_table(
        "program_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("week_number", sa.Integer, nullable=False),
        sa.Column("day_of_week", sa.Text, nullable=True),
        sa.Column("planned_date", sa.Text, nullable=True),
        sa.Column("session_slot_label", sa.Text, nullable=True),
        sa.Column("focus_template", sa.Text, nullable=True),
        sa.Column("sequence_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("session_index", sa.Integer, nullable=True),
    )
    op.create_index("ix_program_sessions_program_id", "program_sessions", ["program_id"])


def downgrade() -> None:
    op.drop_table("program_sessions")
    op.drop_table("programs")
    op.drop_index("ix_sessions_session_date", table_name="sessions")

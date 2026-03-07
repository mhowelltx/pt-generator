"""Initial schema: trainers, clients, sessions, goals, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-03-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trainers",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("display_name", sa.Text, nullable=True),
        sa.Column("gym_name", sa.Text, nullable=True),
        sa.Column("contact_info", sa.Text, nullable=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("dev_mode", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, sa.ForeignKey("trainers.user_id"), nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("client_name", sa.Text, nullable=False),
        sa.Column("constraints", JSONB, nullable=False, server_default="'[]'"),
        sa.Column("preferred_equipment", JSONB, nullable=False, server_default="'[]'"),
        sa.Column("machine_settings", JSONB, nullable=False, server_default="'{}'"),
        sa.Column("notes", sa.Text, nullable=False, server_default="''"),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "slug", name="uq_clients_user_slug"),
    )
    op.create_index("ix_clients_user_id", "clients", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("session_date", sa.Text, nullable=True),
        sa.Column("session_number", sa.Integer, nullable=True),
        sa.Column("focus", sa.Text, nullable=True),
        sa.Column("loads", JSONB, nullable=False, server_default="'{}'"),
        sa.Column("actual_loads", JSONB, nullable=False, server_default="'{}'"),
        sa.Column("plan_json", JSONB, nullable=True),
        sa.Column("trainer_notes", sa.Text, nullable=False, server_default="''"),
        sa.Column("archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_sessions_client_id", "sessions", ["client_id"])

    op.create_table(
        "goals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("goal_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_goals_client_id", "goals", ["client_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # Not a FK — audit must survive trainer deletion
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default="''"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("goals")
    op.drop_table("sessions")
    op.drop_table("clients")
    op.drop_table("trainers")

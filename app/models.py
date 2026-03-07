"""SQLAlchemy ORM models for PT Generator."""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Trainer(Base):
    __tablename__ = "trainers"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    gym_name: Mapped[str | None] = mapped_column(Text)
    contact_info: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    dev_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    clients: Mapped[list[Client]] = relationship("Client", back_populates="trainer")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("trainers.user_id"), nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    client_name: Mapped[str] = mapped_column(Text, nullable=False)
    constraints: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    preferred_equipment: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    machine_settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    trainer: Mapped[Trainer] = relationship("Trainer", back_populates="clients")
    sessions: Mapped[list[Session]] = relationship(
        "Session", back_populates="client", order_by="Session.id"
    )
    goals: Mapped[list[Goal]] = relationship(
        "Goal", back_populates="client", order_by="Goal.id"
    )

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("user_id", "slug", name="uq_clients_user_slug"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    session_date: Mapped[str | None] = mapped_column(Text)
    session_number: Mapped[int | None] = mapped_column(Integer)
    focus: Mapped[str | None] = mapped_column(Text)
    loads: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="'{}'")
    actual_loads: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="'{}'")
    plan_json: Mapped[dict | None] = mapped_column(JSONB)
    trainer_notes: Mapped[str] = mapped_column(Text, default="", server_default="''")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped[Client] = relationship("Client", back_populates="sessions")


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    goal_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped[Client] = relationship("Client", back_populates="goals")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", server_default="''")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

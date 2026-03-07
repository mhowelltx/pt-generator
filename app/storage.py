"""Storage layer — all persistence goes through these functions.

Previously file-based (JSON on disk); now backed by PostgreSQL via SQLAlchemy.
Public function signatures are unchanged so callers (routes, services) need no
modifications.
"""
from __future__ import annotations

import datetime
import re

from sqlalchemy import select, delete, update, func

from app.database import SessionLocal
from app.models import AuditLog, Client, Goal, Session, Trainer

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Slug utility
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    """Convert a client name to a URL/DB-safe slug."""
    return re.sub(r"[^\w]+", "_", name.strip().lower()).strip("_")


# Keep private alias for internal use (referenced by demo_seed.py)
_slug = slug


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def migrate_profile(profile: dict) -> dict:
    """Apply forward migrations to an old profile dict and return it."""
    version = profile.get("schema_version", 0)
    if version < 1:
        profile.setdefault("notes", "")
        profile["schema_version"] = 1
    return profile


def _client_to_profile(client: Client) -> dict:
    """Convert a Client ORM row to the profile dict shape expected by callers."""
    return {
        "client_name": client.client_name,
        "constraints": client.constraints or [],
        "preferred_equipment": client.preferred_equipment or [],
        "machine_settings": client.machine_settings or {},
        "notes": client.notes or "",
        "schema_version": client.schema_version,
    }


def profile_exists(name: str, user_id: str | None = None) -> bool:
    with SessionLocal() as db:
        row = db.execute(
            select(Client.id).where(
                Client.user_id == user_id,
                Client.slug == _slug(name),
                Client.deleted_at.is_(None),
            )
        ).first()
        return row is not None


def load_profile(name: str, user_id: str | None = None) -> dict:
    with SessionLocal() as db:
        client = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.slug == _slug(name),
                Client.deleted_at.is_(None),
            )
        ).scalar_one()
        return migrate_profile(_client_to_profile(client))


def save_profile(name: str, profile: dict, user_id: str | None = None) -> None:
    """Upsert a client profile. Creates the trainer row if needed."""
    s = _slug(name)
    with SessionLocal() as db:
        # Ensure the trainer row exists (required by FK)
        _ensure_trainer(db, user_id)

        client = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.slug == s,
            )
        ).scalar_one_or_none()

        if client is None:
            client = Client(user_id=user_id, slug=s)
            db.add(client)

        client.client_name = profile.get("client_name", name)
        client.constraints = profile.get("constraints", [])
        client.preferred_equipment = profile.get("preferred_equipment", [])
        client.machine_settings = profile.get("machine_settings", {})
        client.notes = profile.get("notes", "")
        client.schema_version = SCHEMA_VERSION
        client.deleted_at = None  # un-delete if previously soft-deleted
        db.commit()


def scaffold_profile(name: str, user_id: str | None = None) -> dict:
    """Create and persist a blank profile for a new client."""
    profile = {
        "client_name": name,
        "constraints": [],
        "preferred_equipment": [],
        "machine_settings": {},
        "notes": "",
    }
    save_profile(name, profile, user_id)
    return profile


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _session_to_entry(session: Session) -> dict:
    """Convert a Session ORM row to the history-entry dict shape."""
    entry: dict = {}
    if session.plan_json:
        entry.update(session.plan_json)
    # Overlay structured columns so they're always authoritative
    if session.session_date:
        entry["session_date"] = session.session_date
    if session.session_number is not None:
        entry["session_number"] = session.session_number
    if session.focus:
        entry["focus"] = session.focus
    entry["loads"] = session.loads or {}
    entry["actual_loads"] = session.actual_loads or {}
    entry["trainer_notes"] = session.trainer_notes or ""
    entry["archived"] = session.archived
    return entry


def _get_client(db, name: str, user_id: str | None) -> Client | None:
    return db.execute(
        select(Client).where(
            Client.user_id == user_id,
            Client.slug == _slug(name),
            Client.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def load_history(name: str, user_id: str | None = None) -> list:
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return []
        sessions = db.execute(
            select(Session)
            .where(Session.client_id == client.id)
            .order_by(Session.id)
        ).scalars().all()
        return [_session_to_entry(s) for s in sessions]


def save_history(name: str, history: list, user_id: str | None = None) -> None:
    """Replace all session rows for a client atomically."""
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return
        # Delete existing rows and re-insert — wrapped in one transaction
        db.execute(delete(Session).where(Session.client_id == client.id))
        for entry in history:
            db.add(_entry_to_session(client.id, entry))
        db.commit()


def append_history(name: str, entry: dict, user_id: str | None = None) -> None:
    """Append a single session entry without loading the full history."""
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return
        db.add(_entry_to_session(client.id, entry))
        db.commit()


def _entry_to_session(client_id: int, entry: dict) -> Session:
    """Build a Session ORM row from a history-entry dict."""
    return Session(
        client_id=client_id,
        session_date=entry.get("session_date"),
        session_number=entry.get("session_number"),
        focus=entry.get("focus"),
        loads=entry.get("loads", {}),
        actual_loads=entry.get("actual_loads", {}),
        plan_json=entry,
        trainer_notes=entry.get("trainer_notes", ""),
        archived=entry.get("archived", False),
    )


def archive_session(name: str, index: int, user_id: str | None = None) -> bool:
    """Mark the nth session (0-based) as archived."""
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return False
        sessions = db.execute(
            select(Session)
            .where(Session.client_id == client.id)
            .order_by(Session.id)
        ).scalars().all()
        if not (0 <= index < len(sessions)):
            return False
        sessions[index].archived = True
        db.commit()
        return True


# ---------------------------------------------------------------------------
# Goals helpers
# ---------------------------------------------------------------------------

def load_goals(name: str, user_id: str | None = None) -> list:
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return []
        goals = db.execute(
            select(Goal).where(Goal.client_id == client.id).order_by(Goal.id)
        ).scalars().all()
        return [g.goal_json for g in goals]


def save_goals(name: str, goals: list, user_id: str | None = None) -> None:
    """Replace all goal rows for a client atomically."""
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return
        db.execute(delete(Goal).where(Goal.client_id == client.id))
        for goal_dict in goals:
            db.add(Goal(client_id=client.id, goal_json=goal_dict))
        db.commit()


# ---------------------------------------------------------------------------
# Client listing / lookup
# ---------------------------------------------------------------------------

def list_clients(user_id: str | None = None) -> list[dict]:
    """Return summary dicts for all active clients, sorted by name."""
    with SessionLocal() as db:
        clients = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.deleted_at.is_(None),
            )
        ).scalars().all()

        results = []
        for client in clients:
            sessions = db.execute(
                select(Session)
                .where(Session.client_id == client.id)
                .order_by(Session.id)
            ).scalars().all()
            last_session = _session_to_entry(sessions[-1]) if sessions else None
            results.append({
                "slug": client.slug,
                "client_name": client.client_name,
                "session_count": len(sessions),
                "last_session": last_session,
            })

        results.sort(key=lambda x: x["client_name"].lower())
        return results


def load_by_slug(slug: str, user_id: str | None = None) -> tuple[dict, list] | None:
    """Return (profile, history) for a client by slug, or None if not found."""
    with SessionLocal() as db:
        client = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.slug == slug,
                Client.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if client is None:
            return None
        profile = migrate_profile(_client_to_profile(client))
        sessions = db.execute(
            select(Session)
            .where(Session.client_id == client.id)
            .order_by(Session.id)
        ).scalars().all()
        history = [_session_to_entry(s) for s in sessions]
        return profile, history


def soft_delete_client(name: str, user_id: str | None = None):
    """Soft-delete a client by setting deleted_at."""
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return
        client.deleted_at = datetime.datetime.utcnow().replace(
            tzinfo=datetime.timezone.utc
        )
        db.commit()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def append_audit_log(user_id: str, event: str, detail: str = "") -> None:
    with SessionLocal() as db:
        db.add(AuditLog(user_id=user_id, event=event, detail=detail))
        db.commit()


# ---------------------------------------------------------------------------
# Trainer profile
# ---------------------------------------------------------------------------

def _ensure_trainer(db, user_id: str) -> None:
    """Insert a bare trainer row if one doesn't exist yet."""
    exists = db.execute(
        select(Trainer.user_id).where(Trainer.user_id == user_id)
    ).first()
    if not exists:
        db.add(Trainer(user_id=user_id))
        db.flush()


def load_trainer_profile(user_id: str) -> dict:
    """Load trainer profile data. Returns empty dict if not yet created."""
    with SessionLocal() as db:
        trainer = db.execute(
            select(Trainer).where(Trainer.user_id == user_id)
        ).scalar_one_or_none()
        if trainer is None:
            return {}
        return {
            "display_name": trainer.display_name or "",
            "gym_name": trainer.gym_name or "",
            "contact_info": trainer.contact_info or "",
            "bio": trainer.bio or "",
            "dev_mode": trainer.dev_mode,
        }


def save_trainer_profile(user_id: str, profile: dict) -> None:
    """Upsert trainer profile data."""
    with SessionLocal() as db:
        trainer = db.execute(
            select(Trainer).where(Trainer.user_id == user_id)
        ).scalar_one_or_none()
        if trainer is None:
            trainer = Trainer(user_id=user_id)
            db.add(trainer)
        trainer.display_name = profile.get("display_name")
        trainer.gym_name = profile.get("gym_name")
        trainer.contact_info = profile.get("contact_info")
        trainer.bio = profile.get("bio")
        trainer.dev_mode = bool(profile.get("dev_mode", False))
        db.commit()

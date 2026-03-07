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
from app.models import AuditLog, Client, Goal, Program, ProgramSession, Session, Trainer

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


def clone_session(
    name: str,
    index: int,
    new_date: str,
    user_id: str | None = None,
) -> int | None:
    """Clone session at *index* into a new session with *new_date*.

    The clone inherits the source plan_json and carries over actual_loads (or
    loads if actual_loads is empty) as the new planned loads so progressive
    overload is preserved.  Returns the new session's index in history, or None
    if the source could not be found.
    """
    with SessionLocal() as db:
        client = _get_client(db, name, user_id)
        if client is None:
            return None
        sessions = db.execute(
            select(Session)
            .where(Session.client_id == client.id)
            .order_by(Session.id)
        ).scalars().all()
        if not (0 <= index < len(sessions)):
            return None
        src = sessions[index]

        # Carry over actual_loads as new planned loads (fall back to loads)
        new_loads = src.actual_loads if src.actual_loads else (src.loads or {})

        # Patch plan_json: update date and clear actual results
        plan_json = dict(src.plan_json) if src.plan_json else {}
        if "meta" in plan_json:
            plan_json["meta"] = dict(plan_json["meta"])
            plan_json["meta"]["session_date"] = new_date
            # Bump session number to next available
            existing_max = max((s.session_number or 0) for s in sessions)
            next_num = existing_max + 1
            plan_json["meta"]["session_number"] = next_num

        # Update loading.prior_load_lbs in plan_json for each exercise
        import copy
        plan_json = copy.deepcopy(plan_json)
        for block in plan_json.get("blocks", []):
            for ex in block.get("exercises", []):
                ex_name = ex.get("name", "")
                prior = new_loads.get(ex_name)
                if "loading" not in ex or ex["loading"] is None:
                    ex["loading"] = {}
                if prior is not None:
                    ex["loading"]["prior_load_lbs"] = prior
                    ex["loading"]["load_lbs"] = prior
                # Clear previously recorded actual results
                ex["loading"].pop("reps_achieved", None)

        new_session = Session(
            client_id=client.id,
            session_date=new_date,
            session_number=plan_json.get("meta", {}).get("session_number"),
            focus=src.focus,
            loads=new_loads,
            actual_loads={},
            plan_json=plan_json,
            trainer_notes="",
            archived=False,
        )
        db.add(new_session)
        db.commit()
        db.refresh(new_session)

        # Return new index (count rows after commit)
        all_sessions = db.execute(
            select(Session)
            .where(Session.client_id == client.id)
            .order_by(Session.id)
        ).scalars().all()
        return len(all_sessions) - 1


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


def get_sessions_by_date_range(
    user_id: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Return sessions for all of a trainer's clients within [start_date, end_date].

    Each entry includes client_name, client_slug, session_date, session_number,
    focus, actual_loads (to determine completion status), and session index.
    Archived sessions are excluded.
    """
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
            for idx, s in enumerate(sessions):
                if s.archived:
                    continue
                d = s.session_date or ""
                if d and start_date <= d <= end_date:
                    results.append({
                        "client_name": client.client_name,
                        "client_slug": client.slug,
                        "session_date": d,
                        "session_number": s.session_number,
                        "focus": s.focus or "",
                        "has_actual_loads": bool(s.actual_loads),
                        "session_index": idx,
                    })

        return results


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


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

def _program_to_dict(program: Program) -> dict:
    return {
        "id": program.id,
        "client_id": program.client_id,
        "name": program.name,
        "description": program.description or "",
        "goal_focus": program.goal_focus or "",
        "start_date": program.start_date or "",
        "end_date": program.end_date or "",
        "weeks": program.weeks,
        "sessions_per_week": program.sessions_per_week,
        "status": program.status,
        "program_json": program.program_json or {},
        "created_at": program.created_at.isoformat() if program.created_at else "",
    }


def _program_session_to_dict(ps: ProgramSession) -> dict:
    return {
        "id": ps.id,
        "program_id": ps.program_id,
        "session_id": ps.session_id,
        "session_index": ps.session_index,
        "week_number": ps.week_number,
        "day_of_week": ps.day_of_week or "",
        "planned_date": ps.planned_date or "",
        "session_slot_label": ps.session_slot_label or "",
        "focus_template": ps.focus_template or "",
        "sequence_order": ps.sequence_order,
    }


def create_program(
    client_slug: str,
    user_id: str,
    name: str,
    description: str = "",
    goal_focus: str = "",
    start_date: str = "",
    weeks: int = 4,
    sessions_per_week: int = 3,
    program_json: dict | None = None,
) -> dict | None:
    """Create a program and its session slots. Returns the created program dict."""
    with SessionLocal() as db:
        client = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.slug == client_slug,
                Client.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if client is None:
            return None

        # Compute end_date from start_date + weeks
        end_date = ""
        if start_date:
            import datetime as _dt
            try:
                sd = _dt.date.fromisoformat(start_date)
                ed = sd + _dt.timedelta(weeks=weeks)
                end_date = ed.isoformat()
            except ValueError:
                pass

        program = Program(
            client_id=client.id,
            user_id=user_id,
            name=name,
            description=description,
            goal_focus=goal_focus,
            start_date=start_date,
            end_date=end_date,
            weeks=weeks,
            sessions_per_week=sessions_per_week,
            status="draft",
            program_json=program_json or {},
        )
        db.add(program)
        db.flush()  # get program.id

        # Create session slots from program_json outline
        outline_weeks = (program_json or {}).get("weeks", [])
        seq = 0
        for week_data in outline_weeks:
            wn = week_data.get("week_number", 1)
            for slot in week_data.get("sessions", []):
                ps = ProgramSession(
                    program_id=program.id,
                    week_number=wn,
                    day_of_week=slot.get("day_of_week", ""),
                    planned_date=slot.get("planned_date", ""),
                    session_slot_label=slot.get("label", f"Week {wn}"),
                    focus_template=slot.get("focus", ""),
                    sequence_order=seq,
                )
                db.add(ps)
                seq += 1

        db.commit()
        db.refresh(program)
        return _program_to_dict(program)


def load_program(program_id: int, user_id: str) -> tuple[dict, list] | None:
    """Load a program and its session slots. Returns (program_dict, slots) or None."""
    with SessionLocal() as db:
        program = db.execute(
            select(Program).where(
                Program.id == program_id,
                Program.user_id == user_id,
            )
        ).scalar_one_or_none()
        if program is None:
            return None
        slots = db.execute(
            select(ProgramSession)
            .where(ProgramSession.program_id == program_id)
            .order_by(ProgramSession.sequence_order)
        ).scalars().all()
        return _program_to_dict(program), [_program_session_to_dict(s) for s in slots]


def list_programs(client_slug: str, user_id: str) -> list[dict]:
    """Return all programs for a client, ordered by id desc."""
    with SessionLocal() as db:
        client = db.execute(
            select(Client).where(
                Client.user_id == user_id,
                Client.slug == client_slug,
                Client.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if client is None:
            return []
        programs = db.execute(
            select(Program)
            .where(Program.client_id == client.id)
            .order_by(Program.id.desc())
        ).scalars().all()
        results = []
        for p in programs:
            d = _program_to_dict(p)
            # Count completed vs total slots
            slots = db.execute(
                select(ProgramSession)
                .where(ProgramSession.program_id == p.id)
            ).scalars().all()
            d["total_slots"] = len(slots)
            d["completed_slots"] = sum(1 for s in slots if s.session_id is not None)
            results.append(d)
        return results


def link_session_to_slot(slot_id: int, session_id: int, user_id: str, session_index: int | None = None) -> bool:
    """Link a generated session to a program slot."""
    with SessionLocal() as db:
        ps = db.execute(
            select(ProgramSession).where(ProgramSession.id == slot_id)
        ).scalar_one_or_none()
        if ps is None:
            return False
        # Verify ownership via program
        program = db.execute(
            select(Program).where(
                Program.id == ps.program_id,
                Program.user_id == user_id,
            )
        ).scalar_one_or_none()
        if program is None:
            return False
        ps.session_id = session_id
        if session_index is not None:
            ps.session_index = session_index
        # If all slots now filled, mark program active→completed
        all_slots = db.execute(
            select(ProgramSession).where(ProgramSession.program_id == program.id)
        ).scalars().all()
        if all(s.session_id is not None for s in all_slots):
            program.status = "completed"
        elif program.status == "draft":
            program.status = "active"
        db.commit()
        return True


def update_program_status(program_id: int, status: str, user_id: str) -> bool:
    """Update a program's status."""
    with SessionLocal() as db:
        program = db.execute(
            select(Program).where(
                Program.id == program_id,
                Program.user_id == user_id,
            )
        ).scalar_one_or_none()
        if program is None:
            return False
        program.status = status
        db.commit()
        return True


def delete_program(program_id: int, user_id: str) -> bool:
    """Delete a program and its slots."""
    with SessionLocal() as db:
        program = db.execute(
            select(Program).where(
                Program.id == program_id,
                Program.user_id == user_id,
            )
        ).scalar_one_or_none()
        if program is None:
            return False
        db.execute(
            delete(ProgramSession).where(
                ProgramSession.program_id == program_id
            )
        )
        db.delete(program)
        db.commit()
        return True

"""Demo data seeding for PT Generator.

Seeding is triggered by the ``/auth/demo`` route, which logs in as the
shared ``_demo`` user and seeds a full roster of demo clients from the
committed ``demo_data/clients/`` directory into that user's data store.

The seed is idempotent: if the demo user already has any clients in the
database, seeding is skipped.

Date handling
-------------
Each data file may include an ``anchor_date`` field (ISO format YYYY-MM-DD).
At seed time all dates are shifted by ``(today - anchor_date)`` days so the
demo always presents as "recent" regardless of when it is first loaded.
Past sessions (after shifting) have their ``loads`` copied to ``actual_loads``
to appear as completed in the calendar and history.
"""

from __future__ import annotations

import copy
import datetime
import json
import logging
from pathlib import Path

from app import storage
from app.database import SessionLocal
from app.models import Client as _Client
from app.models import Session as _Session
from sqlalchemy import select

log = logging.getLogger(__name__)

_DEMO_DATA_DIR = Path(__file__).parent.parent / "demo_data" / "clients"


# ---------------------------------------------------------------------------
# Date-shifting helpers
# ---------------------------------------------------------------------------

def _shift_date(date_str: str, delta: int) -> str:
    try:
        d = datetime.date.fromisoformat(date_str)
        return (d + datetime.timedelta(days=delta)).isoformat()
    except (ValueError, TypeError):
        return date_str


def _compute_delta(anchor_date_str: str | None) -> int:
    if not anchor_date_str:
        return 0
    try:
        anchor = datetime.date.fromisoformat(anchor_date_str)
        return (datetime.date.today() - anchor).days
    except ValueError:
        return 0


def _redate_history(entries: list, delta: int, today_iso: str) -> list:
    result = []
    for entry in entries:
        e = dict(entry)
        if e.get("session_date"):
            e["session_date"] = _shift_date(e["session_date"], delta)
        if isinstance(e.get("plan_json"), dict):
            pj = copy.deepcopy(e["plan_json"])
            if isinstance(pj.get("meta"), dict) and pj["meta"].get("session_date"):
                pj["meta"]["session_date"] = _shift_date(pj["meta"]["session_date"], delta)
            e["plan_json"] = pj
        # Auto-complete past sessions
        if (
            e.get("session_date")
            and e["session_date"] < today_iso
            and e.get("loads")
            and not e.get("actual_loads")
        ):
            e["actual_loads"] = dict(e["loads"])
        result.append(e)
    return result


def _redate_goals(goals: list, delta: int) -> list:
    result = []
    for g in goals:
        g2 = dict(g)
        for field in ("target_date", "created", "achieved_date"):
            if g2.get(field):
                g2[field] = _shift_date(g2[field], delta)
        result.append(g2)
    return result


def _redate_program_json(program_json: dict, delta: int) -> dict:
    pj = copy.deepcopy(program_json)
    for week in pj.get("weeks", []):
        for slot in week.get("sessions", []):
            if slot.get("planned_date"):
                slot["planned_date"] = _shift_date(slot["planned_date"], delta)
    return pj


# ---------------------------------------------------------------------------
# Goals seeding
# ---------------------------------------------------------------------------

def _seed_goals(client_dir: Path, client_name: str, user_id: str, delta: int) -> int:
    goals_path = client_dir / "goals.json"
    if not goals_path.exists():
        return 0
    with goals_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    goals = raw.get("goals", []) if isinstance(raw, dict) else raw
    goals = _redate_goals(goals, delta)
    storage.save_goals(client_name, goals, user_id=user_id)
    return len(goals)


# ---------------------------------------------------------------------------
# Programs seeding
# ---------------------------------------------------------------------------

def _seed_programs(
    client_dir: Path,
    client_slug: str,
    client_name: str,
    user_id: str,
    delta: int,
    today_iso: str,
) -> int:
    programs_path = client_dir / "programs.json"
    if not programs_path.exists():
        return 0
    with programs_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    programs_data = raw.get("programs", []) if isinstance(raw, dict) else raw

    with SessionLocal() as db:
        client_row = db.execute(
            select(_Client).where(
                _Client.user_id == user_id,
                _Client.slug == client_slug,
                _Client.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if client_row is None:
            log.warning("Cannot seed programs — client %s not found.", client_slug)
            return 0
        all_db_sessions = list(
            db.execute(
                select(_Session)
                .where(_Session.client_id == client_row.id)
                .order_by(_Session.id)
            ).scalars().all()
        )

    past_db_sessions = [
        s for s in all_db_sessions
        if s.session_date and s.session_date < today_iso
    ]

    count = 0
    for prog_data in programs_data:
        start_date = _shift_date(prog_data["start_date"], delta) if prog_data.get("start_date") else ""
        program_json = _redate_program_json(prog_data.get("program_json", {}), delta)

        program = storage.create_program(
            client_slug=client_slug,
            user_id=user_id,
            name=prog_data["name"],
            description=prog_data.get("description", ""),
            goal_focus=prog_data.get("goal_focus", ""),
            start_date=start_date,
            weeks=prog_data.get("weeks", 8),
            sessions_per_week=prog_data.get("sessions_per_week", 2),
            program_json=program_json,
        )
        if program is None:
            log.warning("Could not create program '%s' for %s.", prog_data["name"], client_slug)
            continue

        linked_count = prog_data.get("linked_session_count", 0)
        if linked_count > 0 and past_db_sessions:
            _, slots = storage.load_program(program["id"], user_id)
            to_link = past_db_sessions[-linked_count:]
            for slot, sess in zip(slots[:len(to_link)], to_link):
                sess_idx = all_db_sessions.index(sess)
                storage.link_session_to_slot(slot["id"], sess.id, user_id, session_index=sess_idx)

        log.info(
            "Seeded program '%s' for %s (%d slots linked).",
            prog_data["name"], client_slug, min(linked_count, len(past_db_sessions)),
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_seeded(user_id: str) -> bool:
    """Return True if demo data has already been seeded for this user."""
    return len(storage.list_clients(user_id)) > 0


def seed_demo_data(user_id: str) -> int:
    """Seed demo clients, sessions, goals, and programs for *user_id*.

    Returns the number of clients seeded (0 if already seeded).
    """
    if is_seeded(user_id):
        log.debug("Demo data already seeded for user %s — skipping.", user_id)
        return 0

    if not _DEMO_DATA_DIR.exists():
        log.warning("demo_data/clients/ not found at %s — skipping.", _DEMO_DATA_DIR)
        return 0

    today_iso = datetime.date.today().isoformat()
    count = 0

    for client_dir in sorted(_DEMO_DATA_DIR.iterdir()):
        if not client_dir.is_dir():
            continue
        profile_path = client_dir / "profile.json"
        if not profile_path.exists():
            continue

        with profile_path.open(encoding="utf-8") as f:
            profile = json.load(f)

        client_name: str = profile.get("client_name", "")
        if not client_name:
            log.warning("Skipping %s — profile missing client_name.", client_dir.name)
            continue

        storage.save_profile(client_name, profile, user_id=user_id)

        # --- History ---
        history: list = []
        delta = 0
        history_path = client_dir / "history.json"
        if history_path.exists():
            with history_path.open(encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                delta = _compute_delta(raw.get("anchor_date"))
                entries = raw.get("entries", [])
            else:
                entries = raw
            history = _redate_history(entries, delta, today_iso)

        storage.save_history(client_name, history, user_id=user_id)
        log.info("Seeded '%s' (%d sessions, delta=%+d d).", client_name, len(history), delta)

        client_slug = storage.slug(client_name)

        # --- Goals ---
        n_goals = _seed_goals(client_dir, client_name, user_id, delta)
        if n_goals:
            log.info("  → %d goals seeded for %s.", n_goals, client_name)

        # --- Programs ---
        n_programs = _seed_programs(client_dir, client_slug, client_name, user_id, delta, today_iso)
        if n_programs:
            log.info("  → %d programs seeded for %s.", n_programs, client_name)

        count += 1

    log.info("Demo seed complete for %s — %d clients.", user_id, count)
    return count

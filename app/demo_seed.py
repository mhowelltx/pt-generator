"""Demo data seeding for PT Generator.

When the ``DEMO_EMAIL`` environment variable is set and a Google account
whose email matches that value logs in for the first time (no existing
clients), this module seeds a full roster of demo clients from the
committed ``demo_data/clients/`` directory into the user's data store.

The seed is idempotent: a sentinel file ``.demo_seeded`` is written into
the user's client directory after the first successful seed, and
subsequent logins skip the operation entirely.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app import storage

log = logging.getLogger(__name__)

_DEMO_DATA_DIR = Path(__file__).parent.parent / "demo_data" / "clients"
_SENTINEL = ".demo_seeded"

DEMO_EMAIL: str = os.environ.get("DEMO_EMAIL", "").lower().strip()


def is_seeded(user_id: str) -> bool:
    """Return True if demo data has already been seeded for this user."""
    return (storage._base_dir(user_id) / _SENTINEL).exists()


def seed_demo_data(user_id: str) -> int:
    """Seed demo clients and sessions for *user_id*.

    Reads each subdirectory of ``demo_data/clients/`` and writes the
    profile and history into the user's live data directory.

    Returns the number of clients seeded (0 if already seeded or if the
    demo data directory is missing).
    """
    if is_seeded(user_id):
        log.debug("Demo data already seeded for user %s — skipping.", user_id)
        return 0

    if not _DEMO_DATA_DIR.exists():
        log.warning(
            "demo_data/clients/ not found at %s — skipping demo seed.",
            _DEMO_DATA_DIR,
        )
        return 0

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

        history: list = []
        history_path = client_dir / "history.json"
        if history_path.exists():
            with history_path.open(encoding="utf-8") as f:
                history = json.load(f)

        storage.save_history(client_name, history, user_id=user_id)
        log.info("Seeded demo client '%s' (%d sessions).", client_name, len(history))
        count += 1

    # Write sentinel so subsequent logins skip seeding.
    sentinel = storage._base_dir(user_id) / _SENTINEL
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()

    log.info("Demo seed complete for user %s — %d clients seeded.", user_id, count)
    return count

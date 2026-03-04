import json
import os
import re
import shutil
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent.parent / "data" / "clients")))
_TRAINER_DIR = DATA_DIR.parent / "trainer"


def slug(name: str) -> str:
    """Convert a client name to a filesystem-safe directory name."""
    return re.sub(r"[^\w]+", "_", name.strip().lower()).strip("_")


# Keep private alias for internal use
_slug = slug


def _base_dir(user_id: str | None) -> Path:
    return DATA_DIR / user_id if user_id else DATA_DIR


def client_dir(name: str, user_id: str | None = None) -> Path:
    return _base_dir(user_id) / _slug(name)


def profile_exists(name: str, user_id: str | None = None) -> bool:
    return (client_dir(name, user_id) / "profile.json").exists()


def load_profile(name: str, user_id: str | None = None) -> dict:
    path = client_dir(name, user_id) / "profile.json"
    with path.open() as f:
        return json.load(f)


def save_profile(name: str, profile: dict, user_id: str | None = None) -> None:
    path = client_dir(name, user_id) / "profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(profile, f, indent=2)


def load_history(name: str, user_id: str | None = None) -> list:
    path = client_dir(name, user_id) / "history.json"
    if not path.exists():
        return []
    with path.open() as f:
        return json.load(f)


def save_history(name: str, history: list, user_id: str | None = None) -> None:
    path = client_dir(name, user_id) / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(history, f, indent=2)


def delete_client(name: str, user_id: str | None = None) -> None:
    """Permanently delete all data for a client."""
    d = client_dir(name, user_id)
    if d.exists():
        shutil.rmtree(d)


def append_history(name: str, entry: dict, user_id: str | None = None) -> None:
    """Append one session entry to the client's history log."""
    history = load_history(name, user_id)
    history.append(entry)
    save_history(name, history, user_id)


def list_clients(user_id: str | None = None) -> list[dict]:
    """Return summary dicts for all known clients, sorted by name."""
    base = _base_dir(user_id)
    if not base.exists():
        return []
    results = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        profile_path = d / "profile.json"
        if not profile_path.exists():
            continue
        with profile_path.open() as f:
            profile = json.load(f)
        history_path = d / "history.json"
        history: list = []
        if history_path.exists():
            with history_path.open() as f:
                history = json.load(f)
        results.append({
            "slug": d.name,
            "client_name": profile.get("client_name", d.name),
            "session_count": len(history),
            "last_session": history[-1] if history else None,
        })
    results.sort(key=lambda x: x["client_name"].lower())
    return results


def load_by_slug(slug: str, user_id: str | None = None) -> tuple[dict, list] | None:
    """Load (profile, history) for a client identified by their directory slug.

    Returns ``None`` if the slug does not exist.
    """
    d = _base_dir(user_id) / slug
    profile_path = d / "profile.json"
    if not profile_path.exists():
        return None
    with profile_path.open() as f:
        profile = json.load(f)
    history_path = d / "history.json"
    history: list = []
    if history_path.exists():
        with history_path.open() as f:
            history = json.load(f)
    return profile, history


def scaffold_profile(name: str, user_id: str | None = None) -> dict:
    """Create and persist a blank profile scaffold for a new client."""
    profile = {
        "client_name": name,
        "constraints": [],
        "preferred_equipment": [],
        "machine_settings": {},
        "notes": "",
    }
    save_profile(name, profile, user_id)
    save_history(name, [], user_id)
    return profile


def load_trainer_profile(user_id: str) -> dict:
    """Load trainer profile data. Returns empty dict if not yet created."""
    path = _TRAINER_DIR / user_id / "profile.json"
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_trainer_profile(user_id: str, profile: dict) -> None:
    """Persist trainer profile data."""
    path = _TRAINER_DIR / user_id / "profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(profile, f, indent=2)


def archive_session(name: str, index: int, user_id: str | None = None) -> bool:
    """Mark a session entry as archived so it is excluded from progressive overload.

    Returns ``False`` if the index is out of range.
    """
    history = load_history(name, user_id)
    if not (0 <= index < len(history)):
        return False
    history[index]["archived"] = True
    save_history(name, history, user_id)
    return True

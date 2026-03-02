import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "clients"


def _slug(name: str) -> str:
    """Convert a client name to a filesystem-safe directory name."""
    return re.sub(r"[^\w]+", "_", name.strip().lower()).strip("_")


def client_dir(name: str) -> Path:
    return DATA_DIR / _slug(name)


def profile_exists(name: str) -> bool:
    return (client_dir(name) / "profile.json").exists()


def load_profile(name: str) -> dict:
    path = client_dir(name) / "profile.json"
    with path.open() as f:
        return json.load(f)


def save_profile(name: str, profile: dict) -> None:
    path = client_dir(name) / "profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(profile, f, indent=2)


def load_history(name: str) -> list:
    path = client_dir(name) / "history.json"
    if not path.exists():
        return []
    with path.open() as f:
        return json.load(f)


def save_history(name: str, history: list) -> None:
    path = client_dir(name) / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(history, f, indent=2)


def append_history(name: str, entry: dict) -> None:
    """Append one session entry to the client's history log."""
    history = load_history(name)
    history.append(entry)
    save_history(name, history)


def list_clients() -> list[dict]:
    """Return summary dicts for all known clients, sorted by name."""
    if not DATA_DIR.exists():
        return []
    results = []
    for d in DATA_DIR.iterdir():
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


def load_by_slug(slug: str) -> tuple[dict, list] | None:
    """Load (profile, history) for a client identified by their directory slug.

    Returns ``None`` if the slug does not exist.
    """
    d = DATA_DIR / slug
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


def scaffold_profile(name: str) -> dict:
    """Create and persist a blank profile scaffold for a new client."""
    profile = {
        "client_name": name,
        "constraints": [],
        "preferred_equipment": [],
        "machine_settings": {},
        "notes": "",
    }
    save_profile(name, profile)
    save_history(name, [])
    return profile

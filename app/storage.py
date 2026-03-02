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

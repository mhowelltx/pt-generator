import json

from app import config

_system_prompt_cache: str | None = None


def get_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = (
            "You are a strength & conditioning coach and personal trainer that writes NASM-informed session plans.\n"
            "Use the provided tool to output a complete training session plan.\n"
            "If a field's value is unknown or not applicable, use null."
        )
    return _system_prompt_cache


# Keys injected for progression mode — excluded from the inputs JSON block
_PRIOR_KEYS = {"prior_loads", "prior_session_date", "prior_session_number", "prior_progression_notes"}


def build_user_prompt(inputs: dict) -> str:
    """
    inputs keys (recommended):
    - client_name
    - session_date (optional)
    - session_number (optional)
    - duration_minutes (default config.DEFAULT_DURATION)
    - focus
    - constraints (list)
    - equipment_available (list)
    - machine_inventory (optional list like ["Leg Press (Seat 6)", "Chest Press (Seat 3)", ...])
    - preferences (optional list)

    Progression mode keys (optional):
    - prior_loads: dict of {exercise_name: load_lbs} from last session
    - prior_session_date: str
    - prior_session_number: int
    - prior_progression_notes: list[str]
    """

    style_rules = [
        "Use a coach-log format: blocks + exercises with cues, tempo, rest, and machine setup fields where relevant.",
        "Include machine seat/lever/pad settings when machines are used.",
        "Include loading guidance: warm-up set suggestion + working sets; include prior_load_lbs only if provided.",
        "Include at least one regression and one progression for each exercise.",
        "Distribute time_minutes across blocks to total the session duration.",
        "Respect all constraints in the input — do not include exercises that violate them.",
        "Include a cooldown block with breathing and 2–4 stretches.",
    ]

    rules_text = "\n".join(f"- {rule}" for rule in style_rules)
    session_inputs = {k: v for k, v in inputs.items() if k not in _PRIOR_KEYS}
    inputs_json = json.dumps(session_inputs, ensure_ascii=False, indent=2)
    duration = inputs.get("duration_minutes", config.DEFAULT_DURATION)

    prior_section = ""
    if inputs.get("prior_loads"):
        lines = [
            f"\nPrior session data"
            f" (Session #{inputs.get('prior_session_number')}, {inputs.get('prior_session_date')}):",
            "Apply progressive overload — increase load where the client reached the top of the rep range.",
            "Populate prior_load_lbs for each matching exercise using the values below.",
            "",
            "Loads from last session:",
        ]
        for exercise, lbs in inputs["prior_loads"].items():
            lines.append(f"- {exercise}: {lbs} lbs")
        if inputs.get("prior_progression_notes"):
            lines.append("\nProgression notes from last session:")
            for note in inputs["prior_progression_notes"]:
                lines.append(f"- {note}")
        prior_section = "\n".join(lines)

    return f"""Create a {duration}-minute training session plan.

Style & requirements:
{rules_text}

Client / session inputs (authoritative):
{inputs_json}{prior_section}"""

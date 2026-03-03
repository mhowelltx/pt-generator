"""
Shared generation pipeline used by both the CLI (app/main.py) and the web API
(app/web/api.py).  Neither caller should duplicate this logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from anthropic import Anthropic

from app import config, storage
from app.generation import PlanGenerator
from app.schema import TrainingSessionPlan

DEFAULT_EQUIPMENT: list[str] = [
    "dumbbells 5-15",
    "bands",
    "stability ball",
    "step/box",
    "cable machine",
    "selectorized machines",
]

DEFAULT_PREFERENCES: list[str] = [
    "include tempo prescriptions",
    "include rest times",
    "include cues and regressions",
    "include seat/load fields",
]


@dataclass
class GenerationContext:
    """Metadata about the generation run — useful for building user-facing messages."""
    is_new_client: bool
    client_dir: str
    prior_session_number: Optional[int] = None
    prior_session_date: Optional[str] = None
    prior_load_count: int = 0


def parse_list(value: Optional[str]) -> list[str]:
    """Split a comma-separated string into a trimmed list, dropping empty items."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def extract_loads(plan: TrainingSessionPlan) -> dict:
    """Return {exercise_name: load_lbs} for every exercise that has a recorded load."""
    return {
        ex.name: ex.loading.load_lbs
        for block in plan.blocks
        for ex in block.exercises
        if ex.loading and ex.loading.load_lbs is not None
    }


def run_generation(
    *,
    api_key: str,
    client: str,
    focus: str,
    constraints: Optional[list[str]] = None,
    equipment: Optional[list[str]] = None,
    duration: int = 50,
    session_number: Optional[int] = None,
    session_date: Optional[str] = None,
    machine_inventory: Optional[list[str]] = None,
    user_id: Optional[str] = None,
) -> tuple[TrainingSessionPlan, GenerationContext]:
    """
    Full generation pipeline:
      1. Load or scaffold client profile
      2. Resolve constraints / equipment (caller values override profile defaults)
      3. Inject prior session loads from history for progressive overload
      4. Generate plan via Claude
      5. Persist session to history

    Parameters
    ----------
    constraints
        Pass ``None`` to fall back to profile defaults.
        Pass ``[]`` to explicitly request no constraints.
    equipment
        Pass ``None`` to fall back to profile defaults.
    machine_inventory
        Pass ``None`` to use the machine_settings from the client profile.
        Pass ``[]`` to send no machine inventory.
    """
    # --- Profile ---
    if storage.profile_exists(client, user_id=user_id):
        profile = storage.load_profile(client, user_id=user_id)
        is_new = False
    else:
        profile = storage.scaffold_profile(client, user_id=user_id)
        is_new = True

    resolved_constraints: list[str] = (
        constraints if constraints is not None
        else profile.get("constraints", [])
    )

    if equipment is not None:
        resolved_equipment = equipment
    elif profile.get("preferred_equipment"):
        resolved_equipment = profile["preferred_equipment"]
    else:
        resolved_equipment = list(DEFAULT_EQUIPMENT)

    profile_machines = [
        f"{m} ({s})" for m, s in profile.get("machine_settings", {}).items()
    ]
    resolved_machines = (
        machine_inventory if machine_inventory is not None else profile_machines
    )

    inputs: dict = {
        "client_name": client,
        "session_date": session_date or str(date.today()),
        "session_number": session_number,
        "duration_minutes": duration,
        "focus": focus,
        "constraints": resolved_constraints,
        "equipment_available": resolved_equipment,
        "preferences": DEFAULT_PREFERENCES,
    }
    if resolved_machines:
        inputs["machine_inventory"] = resolved_machines
    if profile.get("notes"):
        inputs["trainer_notes"] = profile["notes"]

    # --- Prior history ---
    ctx = GenerationContext(
        is_new_client=is_new,
        client_dir=str(storage.client_dir(client, user_id=user_id)),
    )
    history = storage.load_history(client, user_id=user_id)
    active_history = [h for h in history if not h.get("archived", False)]
    if active_history:
        last = active_history[-1]
        prior_loads = last.get("loads", {})
        inputs["prior_loads"] = prior_loads
        inputs["prior_session_date"] = last.get("session_date")
        inputs["prior_session_number"] = last.get("session_number")
        inputs["prior_progression_notes"] = last.get("progression_notes", [])
        ctx.prior_session_number = last.get("session_number")
        ctx.prior_session_date = last.get("session_date")
        ctx.prior_load_count = len(prior_loads)

    # --- Generate ---
    generator = PlanGenerator(Anthropic(api_key=api_key))
    plan = generator.generate(inputs)

    # --- Persist ---
    storage.append_history(client, {
        "session_date": plan.meta.session_date,
        "session_number": plan.meta.session_number,
        "focus": plan.meta.focus,
        "loads": extract_loads(plan),
        "progression_notes": plan.progression_notes,
        "plan_json": plan.model_dump(),
    }, user_id=user_id)

    return plan, ctx


def suggest_next_focus(
    *,
    api_key: str,
    client: str,
    user_id: Optional[str] = None,
) -> str:
    """Use Claude to suggest a focus for the client's next session based on history."""
    history = storage.load_history(client, user_id=user_id)
    active_history = [h for h in history if not h.get("archived", False)]

    if not active_history:
        return "Full-body strength with balance + core integration."

    profile = storage.load_profile(client, user_id=user_id) if storage.profile_exists(client, user_id=user_id) else {}
    recent = active_history[-5:]
    history_lines = "\n".join(
        f"- Session #{s.get('session_number', '?')} ({s.get('session_date', '?')}): {s.get('focus', '?')}"
        for s in recent
    )
    constraints = profile.get("constraints", [])
    constraint_text = f"\nClient constraints: {', '.join(constraints)}" if constraints else ""

    anthropic_client = Anthropic(api_key=api_key)
    response = anthropic_client.messages.create(
        model=config.MODEL,
        max_tokens=120,
        temperature=0.5,
        messages=[{
            "role": "user",
            "content": (
                f"Based on this personal training client's recent session history, "
                f"suggest one concise focus statement for their next session. "
                f"Respond with just the focus, no explanation.{constraint_text}\n\n"
                f"Recent sessions:\n{history_lines}"
            ),
        }],
    )
    return response.content[0].text.strip()


def generate_progress_summary(
    *,
    api_key: str,
    client_name: str,
    history: list,
    profile: dict,
) -> str:
    """Generate an AI-written monthly progress summary for a client."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    recent = [s for s in history if not s.get("archived", False) and (s.get("session_date") or "") >= cutoff]

    if not recent:
        return "No sessions recorded in the past 30 days."

    sessions_text = "\n\n".join(
        f"Session #{s.get('session_number', '?')} on {s.get('session_date', '?')}: {s.get('focus', '?')}\n"
        f"Progression notes: {'; '.join(s.get('progression_notes', [])) or 'none'}"
        for s in recent
    )
    constraints = profile.get("constraints", [])
    constraint_text = f"\nClient constraints: {', '.join(constraints)}" if constraints else ""

    anthropic_client = Anthropic(api_key=api_key)
    response = anthropic_client.messages.create(
        model=config.MODEL,
        max_tokens=600,
        temperature=0.4,
        messages=[{
            "role": "user",
            "content": (
                f"Write a brief, encouraging monthly progress summary for a personal training client. "
                f"Highlight accomplishments, key progressions, and one or two focus areas for the coming month. "
                f"Keep it under 200 words, plain prose (no bullet points).{constraint_text}\n\n"
                f"Client: {client_name}\n\n"
                f"Sessions this month:\n{sessions_text}"
            ),
        }],
    )
    return response.content[0].text.strip()

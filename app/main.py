import logging
import os
from datetime import date
from enum import Enum
from typing import Optional

import typer
from anthropic import Anthropic
from dotenv import load_dotenv

from app import storage
from app.formatter import print_plan
from app.generation import PlanGenerator
from app.schema import TrainingSessionPlan

app = typer.Typer(help="Generate an AI-powered personal training session plan.")


class ExportFormat(str, Enum):
    none = "none"
    markdown = "markdown"
    docx = "docx"
    both = "both"

_DEFAULT_EQUIPMENT = "dumbbells 5-15,bands,stability ball,step/box,cable machine,selectorized machines"
_DEFAULT_PREFERENCES = [
    "include tempo prescriptions",
    "include rest times",
    "include cues and regressions",
    "include seat/load fields",
]


def _extract_loads(plan: TrainingSessionPlan) -> dict:
    """Return a dict of {exercise_name: load_lbs} for all exercises that have a load recorded."""
    return {
        ex.name: ex.loading.load_lbs
        for block in plan.blocks
        for ex in block.exercises
        if ex.loading and ex.loading.load_lbs is not None
    }


def _parse_list(value: Optional[str]) -> list[str]:
    """Split a comma-separated string into a trimmed list, dropping empty items."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@app.command()
def generate(
    client: str = typer.Option("Sample Client", "--client", help="Client name"),
    focus: str = typer.Option(
        "Full-body strength with balance + core integration.",
        "--focus",
        help="1-line session focus statement",
    ),
    constraints: Optional[str] = typer.Option(
        None,
        "--constraints",
        help="Comma-separated list of constraints or injuries (e.g. 'shoulder pain,knee issues')",
    ),
    equipment: Optional[str] = typer.Option(
        None,
        "--equipment",
        help=f"Comma-separated list of available equipment (default: {_DEFAULT_EQUIPMENT})",
    ),
    duration: int = typer.Option(50, "--duration", help="Session duration in minutes"),
    session_number: Optional[int] = typer.Option(None, "--session-number", help="Session number"),
    session_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Session date in YYYY-MM-DD format (default: today)",
    ),
    export: ExportFormat = typer.Option(
        ExportFormat.none,
        "--export",
        help="Export session to file: markdown, docx, or both",
    ),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    # Load or scaffold client profile
    if storage.profile_exists(client):
        profile = storage.load_profile(client)
        typer.echo(f"Profile loaded: {client}")
    else:
        profile = storage.scaffold_profile(client)
        typer.echo(f"New client '{client}' — profile created at {storage.client_dir(client)}")

    # CLI args take precedence over profile defaults
    resolved_constraints = (
        _parse_list(constraints) if constraints is not None
        else profile.get("constraints", [])
    )
    if equipment is not None:
        resolved_equipment = _parse_list(equipment)
    elif profile.get("preferred_equipment"):
        resolved_equipment = profile["preferred_equipment"]
    else:
        resolved_equipment = _parse_list(_DEFAULT_EQUIPMENT)

    # Build machine_inventory string from profile machine_settings dict
    machine_settings = profile.get("machine_settings", {})
    machine_inventory = [f"{machine} ({seat})" for machine, seat in machine_settings.items()]

    inputs = {
        "client_name": client,
        "session_date": session_date or str(date.today()),
        "session_number": session_number,
        "duration_minutes": duration,
        "focus": focus,
        "constraints": resolved_constraints,
        "equipment_available": resolved_equipment,
        "preferences": _DEFAULT_PREFERENCES,
    }
    if machine_inventory:
        inputs["machine_inventory"] = machine_inventory
    if profile.get("notes"):
        inputs["trainer_notes"] = profile["notes"]

    # Inject prior session loads for progressive overload
    history = storage.load_history(client)
    if history:
        last = history[-1]
        inputs["prior_loads"] = last.get("loads", {})
        inputs["prior_session_date"] = last.get("session_date")
        inputs["prior_session_number"] = last.get("session_number")
        inputs["prior_progression_notes"] = last.get("progression_notes", [])
        typer.echo(
            f"Prior session: #{last.get('session_number')} ({last.get('session_date')}) "
            f"— {len(inputs['prior_loads'])} load(s) found, applying progressive overload"
        )

    api_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    generator = PlanGenerator(api_client)
    plan = generator.generate(inputs)
    print_plan(plan)

    # Persist session to history
    storage.append_history(client, {
        "session_date": plan.meta.session_date,
        "session_number": plan.meta.session_number,
        "focus": plan.meta.focus,
        "loads": _extract_loads(plan),
        "progression_notes": plan.progression_notes,
    })
    typer.echo("Session saved to history.")

    if export in (ExportFormat.markdown, ExportFormat.both):
        from app.export_markdown import export as export_md
        path = export_md(plan)
        typer.echo(f"Markdown saved: {path}")

    if export in (ExportFormat.docx, ExportFormat.both):
        from app.export_docx import export as export_docx
        path = export_docx(plan)
        typer.echo(f"DOCX saved: {path}")


if __name__ == "__main__":
    app()

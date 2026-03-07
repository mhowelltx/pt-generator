import logging
import os
from enum import Enum
from typing import Optional

import typer
from dotenv import load_dotenv

from app import service
from app.formatter import print_plan

app = typer.Typer(help="Generate an AI-powered personal training session plan.")

_DEFAULT_EQUIPMENT_HINT = "dumbbells 5-15,bands,stability ball,step/box,cable machine,selectorized machines"


class ExportFormat(str, Enum):
    none = "none"
    markdown = "markdown"
    docx = "docx"
    both = "both"


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
        help="Comma-separated constraints/injuries (e.g. 'shoulder pain,knee issues')",
    ),
    equipment: Optional[str] = typer.Option(
        None,
        "--equipment",
        help=f"Comma-separated equipment list (default: {_DEFAULT_EQUIPMENT_HINT})",
    ),
    duration: int = typer.Option(50, "--duration", help="Session duration in minutes"),
    session_number: Optional[int] = typer.Option(None, "--session-number", help="Session number"),
    session_date: Optional[str] = typer.Option(
        None, "--date", help="Session date YYYY-MM-DD (default: today)"
    ),
    export: ExportFormat = typer.Option(
        ExportFormat.none, "--export", help="Export format: markdown, docx, or both"
    ),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    plan, ctx = service.run_generation(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        client=client,
        focus=focus,
        constraints=service.parse_list(constraints) if constraints is not None else None,
        equipment=service.parse_list(equipment) if equipment is not None else None,
        duration=duration,
        session_number=session_number,
        session_date=session_date,
    )

    if ctx.is_new_client:
        typer.echo(f"New client '{client}' — profile created")
    else:
        typer.echo(f"Profile loaded: {client}")

    if ctx.prior_load_count:
        typer.echo(
            f"Prior session: #{ctx.prior_session_number} ({ctx.prior_session_date})"
            f" — {ctx.prior_load_count} load(s) found, applying progressive overload"
        )

    print_plan(plan)
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

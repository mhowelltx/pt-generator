import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from tenacity import RetryError

from app import service, storage

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_OUTPUTS_DIR = (Path(__file__).parent.parent.parent / "outputs").resolve()


def _media_type(suffix: str) -> str:
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown; charset=utf-8",
    }.get(suffix.lower(), "application/octet-stream")


def _validate_form(
    client: str,
    focus: str,
    duration: str,
    session_number: str,
    session_date: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not client.strip():
        errors["client"] = "Client name is required."

    if not focus.strip():
        errors["focus"] = "Session focus is required."

    try:
        dur = int(duration)
        if dur < 1 or dur > 180:
            errors["duration"] = "Duration must be between 1 and 180 minutes."
    except (ValueError, TypeError):
        errors["duration"] = "Duration must be a whole number."

    if session_number.strip():
        try:
            sn = int(session_number)
            if sn < 1:
                errors["session_number"] = "Session number must be 1 or greater."
        except ValueError:
            errors["session_number"] = "Session number must be a whole number."

    if session_date.strip():
        try:
            datetime.strptime(session_date.strip(), "%Y-%m-%d")
        except ValueError:
            errors["session_date"] = "Date must be in YYYY-MM-DD format (e.g. 2026-03-01)."

    return errors


def _form_response(request, prev, error=None, errors=None, status_code=200):
    return templates.TemplateResponse("form.html", {
        "request": request,
        "today": str(date.today()),
        "prev": prev,
        "error": error,
        "errors": errors or {},
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def form_page(request: Request, client: str = ""):
    prev = {"client": client} if client else {}
    return _form_response(request, prev)


@router.get("/download")
def download_file(file: str):
    """Serve an exported file. ``file`` must be a path relative to the outputs directory."""
    requested = (_OUTPUTS_DIR / file).resolve()
    if not requested.is_relative_to(_OUTPUTS_DIR):
        raise HTTPException(status_code=403, detail="Access denied.")
    if not requested.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=requested,
        filename=requested.name,
        media_type=_media_type(requested.suffix),
    )


@router.get("/clients", response_class=HTMLResponse)
def clients_list(request: Request):
    clients = storage.list_clients()
    return templates.TemplateResponse("clients.html", {
        "request": request,
        "clients": clients,
    })


@router.get("/clients/{slug}", response_class=HTMLResponse)
def client_detail(request: Request, slug: str):
    result = storage.load_by_slug(slug)
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    return templates.TemplateResponse("client_detail.html", {
        "request": request,
        "slug": slug,
        "profile": profile,
        "history": list(reversed(history)),
    })


@router.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    client: Annotated[str, Form()],
    focus: Annotated[str, Form()],
    duration: Annotated[str, Form()],
    session_number: Annotated[str, Form()] = "",
    session_date: Annotated[str, Form()] = "",
    constraints: Annotated[str, Form()] = "",
    equipment: Annotated[str, Form()] = "",
    include_machine_inventory: Annotated[Optional[str], Form()] = None,
    machine_inventory: Annotated[str, Form()] = "",
    export_docx: Annotated[Optional[str], Form()] = None,
    export_markdown: Annotated[Optional[str], Form()] = None,
):
    prev = {
        "client": client,
        "session_number": session_number,
        "focus": focus,
        "duration": duration,
        "session_date": session_date,
        "constraints": constraints,
        "equipment": equipment,
        "include_machine_inventory": include_machine_inventory is not None,
        "machine_inventory": machine_inventory,
        "export_docx": export_docx is not None,
        "export_markdown": export_markdown is not None,
    }

    # --- Validation ---
    field_errors = _validate_form(client, focus, duration, session_number, session_date)
    if field_errors:
        return _form_response(request, prev, errors=field_errors, status_code=422)

    # --- Parse validated values ---
    dur = int(duration)
    sn: Optional[int] = int(session_number) if session_number.strip() else None
    sd: Optional[str] = session_date.strip() or None

    machines: Optional[list[str]] = None
    if include_machine_inventory is not None and machine_inventory.strip():
        machines = [ln.strip() for ln in machine_inventory.splitlines() if ln.strip()]

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _form_response(request, prev,
                              error="ANTHROPIC_API_KEY is not set on the server.",
                              status_code=500)

    # --- Generate ---
    try:
        plan, ctx = service.run_generation(
            api_key=api_key,
            client=client,
            focus=focus,
            constraints=service.parse_list(constraints) if constraints.strip() else None,
            equipment=service.parse_list(equipment) if equipment.strip() else None,
            duration=dur,
            session_number=sn,
            session_date=sd,
            machine_inventory=machines,
        )
    except ValidationError:
        log.warning("Plan schema validation failed")
        return _form_response(request, prev,
                              error="The AI returned an invalid plan structure. Please try again.",
                              status_code=502)
    except RetryError:
        log.error("Generation failed after all retries")
        return _form_response(request, prev,
                              error="Plan generation failed after multiple attempts. Please try again.",
                              status_code=502)
    except Exception as exc:
        log.exception("Unexpected generation error")
        return _form_response(request, prev,
                              error=f"An unexpected error occurred: {exc}",
                              status_code=500)

    # --- Exports ---
    export_links: list[dict] = []
    if export_docx is not None:
        from app.export_docx import export as _export_docx
        path = _export_docx(plan).resolve()
        rel = path.relative_to(_OUTPUTS_DIR)
        export_links.append({
            "label": "Download DOCX",
            "url": f"/download?file={quote(rel.as_posix())}",
            "filename": path.name,
        })
    if export_markdown is not None:
        from app.export_markdown import export as _export_md
        path = _export_md(plan).resolve()
        rel = path.relative_to(_OUTPUTS_DIR)
        export_links.append({
            "label": "Download Markdown",
            "url": f"/download?file={quote(rel.as_posix())}",
            "filename": path.name,
        })

    return templates.TemplateResponse("result.html", {
        "request": request,
        "plan": plan,
        "ctx": ctx,
        "export_links": export_links,
    })

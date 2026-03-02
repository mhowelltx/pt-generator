import logging
import os
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import service, storage

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
def form_page(request: Request, client: str = ""):
    prev = {"client": client} if client else {}
    return templates.TemplateResponse("form.html", {
        "request": request,
        "today": str(date.today()),
        "prev": prev,
        "error": None,
    })


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

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return templates.TemplateResponse("form.html", {
            "request": request,
            "today": str(date.today()),
            "prev": prev,
            "error": "ANTHROPIC_API_KEY is not set.",
        }, status_code=500)

    # Parse numeric fields
    try:
        dur = int(duration)
    except (ValueError, TypeError):
        dur = 50

    sn: Optional[int] = int(session_number) if session_number.strip() else None
    sd: Optional[str] = session_date.strip() or None

    # Parse machine inventory lines
    machines: Optional[list[str]] = None
    if include_machine_inventory is not None and machine_inventory.strip():
        machines = [ln.strip() for ln in machine_inventory.splitlines() if ln.strip()]

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
    except Exception as exc:
        log.exception("Generation failed")
        return templates.TemplateResponse("form.html", {
            "request": request,
            "today": str(date.today()),
            "prev": prev,
            "error": str(exc),
        }, status_code=500)

    export_paths: dict[str, str] = {}
    if export_docx is not None:
        from app.export_docx import export as export_docx_fn
        export_paths["docx"] = str(export_docx_fn(plan))
    if export_markdown is not None:
        from app.export_markdown import export as export_md_fn
        export_paths["markdown"] = str(export_md_fn(plan))

    return templates.TemplateResponse("result.html", {
        "request": request,
        "plan": plan,
        "ctx": ctx,
        "export_paths": export_paths,
    })

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from tenacity import RetryError

from app import service, storage
from app.schema import TrainingSessionPlan
from app.web.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", str(Path(__file__).parent.parent.parent / "outputs"))).resolve()


def _user_outputs_dir(user_id: str) -> Path:
    return _OUTPUTS_DIR / user_id


def _media_type(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
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


def _form_response(request, prev, user, error=None, errors=None, status_code=200):
    return templates.TemplateResponse("form.html", {
        "request": request,
        "today": str(date.today()),
        "prev": prev,
        "error": error,
        "errors": errors or {},
        "user": user,
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def form_page(
    request: Request,
    client: str = "",
    suggested_focus: str = "",
    user: dict = Depends(get_current_user),
):
    prev = {}
    if client:
        prev["client"] = client
    if suggested_focus:
        prev["focus"] = suggested_focus
    return _form_response(request, prev, user)


@router.get("/download")
def download_file(file: str, user: dict = Depends(get_current_user)):
    """Serve an exported file. ``file`` must be a path relative to the outputs directory."""
    user_dir = _user_outputs_dir(user["id"])
    requested = (user_dir / file).resolve()
    if not requested.is_relative_to(user_dir):
        raise HTTPException(status_code=403, detail="Access denied.")
    if not requested.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=requested,
        filename=requested.name,
        media_type=_media_type(requested.suffix),
    )


@router.get("/clients", response_class=HTMLResponse)
def clients_list(request: Request, user: dict = Depends(get_current_user)):
    clients = storage.list_clients(user_id=user["id"])
    return templates.TemplateResponse("clients.html", {
        "request": request,
        "clients": clients,
        "user": user,
    })


@router.get("/clients/new", response_class=HTMLResponse)
def new_client_form(request: Request, user: dict = Depends(get_current_user)):
    return templates.TemplateResponse("client_new.html", {
        "request": request,
        "user": user,
        "error": None,
    })


@router.post("/clients/new", response_class=HTMLResponse)
def new_client_create(
    request: Request,
    client_name: Annotated[str, Form()],
    constraints: Annotated[str, Form()] = "",
    preferred_equipment: Annotated[str, Form()] = "",
    machine_settings_raw: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    client_name = client_name.strip()
    if not client_name:
        return templates.TemplateResponse("client_new.html", {
            "request": request,
            "user": user,
            "error": "Client name is required.",
        }, status_code=422)

    if storage.profile_exists(client_name, user_id=user["id"]):
        return templates.TemplateResponse("client_new.html", {
            "request": request,
            "user": user,
            "error": f'A client named "{client_name}" already exists.',
        }, status_code=422)

    parsed_machines: dict[str, str] = {}
    for line in machine_settings_raw.splitlines():
        if ":" in line:
            k, _, v = line.strip().partition(":")
            if k.strip():
                parsed_machines[k.strip()] = v.strip()

    profile = {
        "client_name": client_name,
        "constraints": [l.strip() for l in constraints.splitlines() if l.strip()],
        "preferred_equipment": [l.strip() for l in preferred_equipment.splitlines() if l.strip()],
        "machine_settings": parsed_machines,
        "notes": notes.strip(),
    }
    storage.save_profile(client_name, profile, user_id=user["id"])
    storage.save_history(client_name, [], user_id=user["id"])

    client_slug = storage.slug(client_name)
    return RedirectResponse(url=f"/clients/{client_slug}", status_code=303)


@router.get("/clients/{slug}", response_class=HTMLResponse)
def client_detail(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    # Preserve original indices for archive actions, then reverse to newest-first
    indexed_history = list(reversed(list(enumerate(history))))
    return templates.TemplateResponse("client_detail.html", {
        "request": request,
        "slug": slug,
        "profile": profile,
        "history": indexed_history,
        "user": user,
    })


@router.post("/clients/{slug}/sessions/{index}/archive")
def session_archive(slug: str, index: int, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    storage.archive_session(profile["client_name"], index, user_id=user["id"])
    return RedirectResponse(url=f"/clients/{slug}", status_code=303)


@router.get("/clients/{slug}/edit", response_class=HTMLResponse)
def client_edit_form(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result
    machines_text = "\n".join(
        f"{m}: {s}" for m, s in profile.get("machine_settings", {}).items()
    )
    return templates.TemplateResponse("client_edit.html", {
        "request": request,
        "slug": slug,
        "profile": profile,
        "machines_text": machines_text,
        "user": user,
    })


@router.post("/clients/{slug}/edit", response_class=HTMLResponse)
def client_edit_save(
    request: Request,
    slug: str,
    constraints: Annotated[str, Form()] = "",
    preferred_equipment: Annotated[str, Form()] = "",
    machine_settings_raw: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result

    parsed_machines: dict[str, str] = {}
    for line in machine_settings_raw.splitlines():
        if ":" in line:
            k, _, v = line.strip().partition(":")
            if k.strip():
                parsed_machines[k.strip()] = v.strip()

    profile["constraints"] = [l.strip() for l in constraints.splitlines() if l.strip()]
    profile["preferred_equipment"] = [l.strip() for l in preferred_equipment.splitlines() if l.strip()]
    profile["machine_settings"] = parsed_machines
    profile["notes"] = notes.strip()

    storage.save_profile(profile["client_name"], profile, user_id=user["id"])
    return RedirectResponse(url=f"/clients/{slug}", status_code=303)


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
    export_pdf: Annotated[Optional[str], Form()] = None,
    user: dict = Depends(get_current_user),
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
        "export_pdf": export_pdf is not None,
    }

    # --- Validation ---
    field_errors = _validate_form(client, focus, duration, session_number, session_date)
    if field_errors:
        return _form_response(request, prev, user, errors=field_errors, status_code=422)

    # --- Parse validated values ---
    dur = int(duration)
    sn: Optional[int] = int(session_number) if session_number.strip() else None
    sd: Optional[str] = session_date.strip() or None

    machines: Optional[list[str]] = None
    if include_machine_inventory is not None and machine_inventory.strip():
        machines = [ln.strip() for ln in machine_inventory.splitlines() if ln.strip()]

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _form_response(request, prev, user,
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
            user_id=user["id"],
        )
    except ValidationError:
        log.warning("Plan schema validation failed")
        return _form_response(request, prev, user,
                              error="The AI returned an invalid plan structure. Please try again.",
                              status_code=502)
    except RetryError:
        log.error("Generation failed after all retries")
        return _form_response(request, prev, user,
                              error="Plan generation failed after multiple attempts. Please try again.",
                              status_code=502)
    except Exception as exc:
        log.exception("Unexpected generation error")
        return _form_response(request, prev, user,
                              error=f"An unexpected error occurred: {exc}",
                              status_code=500)

    # --- Exports ---
    user_out = _user_outputs_dir(user["id"])
    export_links: list[dict] = []
    if export_pdf is not None:
        from app.export_pdf import export as _export_pdf
        path = _export_pdf(plan, outputs_dir=user_out).resolve()
        # relative_to(user_out) avoids doubling the user_id in the download URL
        rel = path.relative_to(user_out)
        export_links.append({
            "label": "Download PDF",
            "url": f"/download?file={quote(rel.as_posix())}",
            "filename": path.name,
        })

    return templates.TemplateResponse("result.html", {
        "request": request,
        "plan": plan,
        "ctx": ctx,
        "export_links": export_links,
        "user": user,
        "back_url": None,
        "client_slug": None,
    })


@router.get("/clients/{slug}/suggest-focus")
def suggest_focus(slug: str, user: dict = Depends(get_current_user)):
    """Return a JSON object with an AI-suggested focus for the client's next session."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set.")
    focus = service.suggest_next_focus(
        api_key=api_key,
        client=profile["client_name"],
        user_id=user["id"],
    )
    return JSONResponse({"focus": focus})


@router.get("/clients/{slug}/progress-summary", response_class=HTMLResponse)
def progress_summary(request: Request, slug: str, user: dict = Depends(get_current_user)):
    """Generate and display an AI monthly progress summary for the client."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set.")
    summary = service.generate_progress_summary(
        api_key=api_key,
        client_name=profile["client_name"],
        history=history,
        profile=profile,
    )
    return templates.TemplateResponse("progress_summary.html", {
        "request": request,
        "slug": slug,
        "profile": profile,
        "summary": summary,
        "user": user,
    })


@router.get("/clients/{slug}/sessions/{index}", response_class=HTMLResponse)
def session_plan_view(request: Request, slug: str, index: int, user: dict = Depends(get_current_user)):
    """Render a past session plan stored in history."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    if index < 0 or index >= len(history):
        return HTMLResponse("<h2>Session not found.</h2>", status_code=404)
    entry = history[index]
    plan_data = entry.get("plan_json")
    if plan_data is None:
        return templates.TemplateResponse("session_not_available.html", {
            "request": request,
            "slug": slug,
            "profile": profile,
            "user": user,
        })
    try:
        plan = TrainingSessionPlan.model_validate(plan_data)
    except ValidationError:
        return HTMLResponse("<h2>Plan data is corrupted.</h2>", status_code=500)
    return templates.TemplateResponse("result.html", {
        "request": request,
        "plan": plan,
        "ctx": None,
        "export_links": [],
        "user": user,
        "back_url": f"/clients/{slug}",
        "client_slug": slug,
    })


@router.post("/clients/{slug}/sessions/{index}/note")
def session_note_save(
    slug: str,
    index: int,
    trainer_notes: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    """Save or clear a trainer note on a history entry."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    if index < 0 or index >= len(history):
        raise HTTPException(status_code=404, detail="Session not found.")
    history[index]["trainer_notes"] = trainer_notes.strip() or None
    storage.save_history(profile["client_name"], history, user_id=user["id"])
    return RedirectResponse(url=f"/clients/{slug}", status_code=303)


@router.get("/clients/{slug}/progress-report-pdf")
def progress_report_pdf(slug: str, user: dict = Depends(get_current_user)):
    """Generate and stream a PDF progress report for the client."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    from app.export_pdf import export_history_report
    user_out = _user_outputs_dir(user["id"])
    path = export_history_report(profile, history, outputs_dir=user_out).resolve()
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/pdf",
    )


@router.get("/profile", response_class=HTMLResponse)
def trainer_profile_page(request: Request, user: dict = Depends(get_current_user)):
    trainer = storage.load_trainer_profile(user["id"])
    return templates.TemplateResponse("trainer_profile.html", {
        "request": request,
        "user": user,
        "trainer": trainer,
        "saved": request.query_params.get("saved") == "1",
    })


@router.post("/profile", response_class=HTMLResponse)
def trainer_profile_save(
    request: Request,
    display_name: Annotated[str, Form()] = "",
    gym_name: Annotated[str, Form()] = "",
    contact_info: Annotated[str, Form()] = "",
    bio: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    storage.save_trainer_profile(user["id"], {
        "display_name": display_name.strip(),
        "gym_name": gym_name.strip(),
        "contact_info": contact_info.strip(),
        "bio": bio.strip(),
    })
    return RedirectResponse(url="/profile?saved=1", status_code=303)

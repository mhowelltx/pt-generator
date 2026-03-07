import io
import json
import logging
import os
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from tenacity import RetryError

from app import service, storage
from app.schema import TrainingSessionPlan
from app.web.auth import get_current_user
from app.web.limiter import limiter

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


_DEMO_AI_CALL_LIMIT = 5


def _demo_ai_gate(user: dict, request: Request, *, json_mode: bool = False):
    """Allow demo users up to _DEMO_AI_CALL_LIMIT AI calls per session.

    Returns None if the call should proceed (counter is incremented).
    Returns a response if the demo limit is exceeded.
    """
    if not user.get("demo"):
        return None
    calls = request.session.get("demo_ai_calls", 0)
    if calls >= _DEMO_AI_CALL_LIMIT:
        if json_mode:
            return JSONResponse({"demo_limit": True})
        return RedirectResponse(url="/login?demo_limit=1", status_code=302)
    request.session["demo_ai_calls"] = calls + 1
    return None

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
def home(user: dict = Depends(get_current_user)):
    return RedirectResponse(url="/clients", status_code=302)


@router.get("/session/new", response_class=HTMLResponse)
def form_page(
    request: Request,
    client: str = "",
    suggested_focus: str = "",
    session_date: str = "",
    user: dict = Depends(get_current_user),
):
    prev = {}
    if client:
        prev["client"] = client
    if suggested_focus:
        prev["focus"] = suggested_focus
    if session_date:
        prev["session_date"] = session_date
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
    storage.append_audit_log(user["id"], "client_create", client_slug)
    return RedirectResponse(url=f"/clients/{client_slug}", status_code=303)


@router.get("/clients/{slug}", response_class=HTMLResponse)
def client_detail(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    # Preserve original indices for archive actions, then reverse to newest-first
    indexed_history = list(reversed(list(enumerate(history))))
    goals = storage.load_goals(profile["client_name"], user_id=user["id"])
    active_goals = [g for g in goals if g.get("status") == "active"]
    return templates.TemplateResponse("client_detail.html", {
        "request": request,
        "slug": slug,
        "profile": profile,
        "history": indexed_history,
        "active_goals": active_goals,
        "user": user,
    })


@router.post("/clients/{slug}/delete")
def client_delete(slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result:
        profile, _ = result
        storage.soft_delete_client(profile["client_name"], user_id=user["id"])
        storage.append_audit_log(user["id"], "client_soft_delete", slug)
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/clients/{slug}/sessions/{index}/copy")
def session_copy(
    slug: str,
    index: int,
    new_date: Annotated[str, Form()],
    user: dict = Depends(get_current_user),
):
    """Clone a past session to a new date."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    try:
        datetime.strptime(new_date.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Date must be YYYY-MM-DD.")
    new_index = storage.clone_session(
        profile["client_name"], index, new_date.strip(), user_id=user["id"]
    )
    if new_index is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    storage.append_audit_log(user["id"], "session_copy", f"{slug}:{index}→{new_index}")
    return RedirectResponse(url=f"/clients/{slug}/sessions/{new_index}", status_code=303)


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
    storage.append_audit_log(user["id"], "client_edit", slug)
    return RedirectResponse(url=f"/clients/{slug}", status_code=303)


@router.post("/generate", response_class=HTMLResponse)
@limiter.limit("10/minute")
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
    if block := _demo_ai_gate(user, request):
        return block

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

    storage.append_audit_log(user["id"], "session_generate", storage.slug(client))

    # --- Link to program slot if session was launched from a program ---
    pending_slot = request.session.pop("pending_program_slot", None)
    if pending_slot and pending_slot.get("client_slug") == storage.slug(client):
        history_after = storage.load_history(client, user_id=user["id"])
        new_session_index = len(history_after) - 1
        # Get the actual session DB id via load_by_slug
        result_for_id = storage.load_by_slug(storage.slug(client), user_id=user["id"])
        if result_for_id:
            from app.database import SessionLocal as _SL
            from app.models import Session as _Sess, Client as _Cli
            from sqlalchemy import select as _sel
            with _SL() as _db:
                _cli = _db.execute(
                    _sel(_Cli).where(
                        _Cli.user_id == user["id"],
                        _Cli.slug == storage.slug(client),
                        _Cli.deleted_at.is_(None),
                    )
                ).scalar_one_or_none()
                if _cli:
                    _sessions = _db.execute(
                        _sel(_Sess).where(_Sess.client_id == _cli.id).order_by(_Sess.id)
                    ).scalars().all()
                    if _sessions:
                        _new_sess_id = _sessions[-1].id
                        _new_sess_idx = len(_sessions) - 1
                        storage.link_session_to_slot(
                            pending_slot["slot_id"], _new_sess_id, user["id"],
                            session_index=_new_sess_idx
                        )

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

    client_slug = storage.slug(client)
    history = storage.load_history(client, user_id=user["id"])
    session_index = len(history) - 1

    return templates.TemplateResponse("result.html", {
        "request": request,
        "plan": plan,
        "ctx": ctx,
        "export_links": export_links,
        "user": user,
        "back_url": f"/clients/{client_slug}",
        "client_slug": client_slug,
        "session_index": session_index,
    })


@router.get("/clients/{slug}/suggest-focus")
@limiter.limit("20/minute")
def suggest_focus(request: Request, slug: str, user: dict = Depends(get_current_user)):
    """Return a JSON object with an AI-suggested focus for the client's next session."""
    if block := _demo_ai_gate(user, request, json_mode=True):
        return block
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
@limiter.limit("5/minute")
def progress_summary(request: Request, slug: str, user: dict = Depends(get_current_user)):
    """Generate and display an AI monthly progress summary for the client."""
    if block := _demo_ai_gate(user, request):
        return block
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
        "session_index": index,
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
    storage.append_audit_log(user["id"], "session_note_edit", f"{slug}:{index}")
    return RedirectResponse(url=f"/clients/{slug}", status_code=303)


@router.get("/clients/{slug}/sessions/{index}/run", response_class=HTMLResponse)
def session_run_view(request: Request, slug: str, index: int, user: dict = Depends(get_current_user)):
    """Live session mode: follow the plan and record actual loads."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    if index < 0 or index >= len(history):
        return HTMLResponse("<h2>Session not found.</h2>", status_code=404)
    entry = history[index]
    plan_data = entry.get("plan_json")
    if plan_data is None:
        return HTMLResponse("<h2>No plan available for this session.</h2>", status_code=404)
    try:
        plan = TrainingSessionPlan.model_validate(plan_data)
    except ValidationError:
        return HTMLResponse("<h2>Plan data is corrupted.</h2>", status_code=500)
    return templates.TemplateResponse("session_run.html", {
        "request": request,
        "profile": profile,
        "slug": slug,
        "index": index,
        "plan": plan,
        "entry": entry,
        "user": user,
    })


@router.post("/clients/{slug}/sessions/{index}/complete")
def session_complete_save(
    slug: str,
    index: int,
    exercise_names: Annotated[List[str], Form()] = [],
    actual_loads: Annotated[List[str], Form()] = [],
    actual_reps_list: Annotated[List[str], Form()] = [],
    planned_load_adj: Annotated[List[str], Form()] = [],
    planned_sets_adj: Annotated[List[str], Form()] = [],
    planned_reps_adj: Annotated[List[str], Form()] = [],
    user: dict = Depends(get_current_user),
):
    """Save actual loads/reps from a live session and detect PRs."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    if index < 0 or index >= len(history):
        raise HTTPException(status_code=404, detail="Session not found.")

    # Build actual loads dict, skipping empty entries
    actual_loads_dict: dict[str, float] = {}
    actual_reps_dict: dict[str, str] = {}
    for name, load_str, reps_str in zip(exercise_names, actual_loads, actual_reps_list):
        if load_str.strip():
            try:
                actual_loads_dict[name] = float(load_str)
            except ValueError:
                pass
        if reps_str.strip():
            actual_reps_dict[name] = reps_str.strip()

    prs = service.detect_prs(history, index, actual_loads_dict)

    # Patch plan_json: update exercise names and any adjusted planned params
    plan_data = history[index].get("plan_json")
    if plan_data:
        flat = [
            (bi, ei)
            for bi, blk in enumerate(plan_data.get("blocks", []))
            for ei in range(len(blk.get("exercises", [])))
        ]
        for pos, (bi, ei) in enumerate(flat):
            ex = plan_data["blocks"][bi]["exercises"][ei]
            if pos < len(exercise_names):
                ex["name"] = exercise_names[pos]
            if pos < len(planned_load_adj) and planned_load_adj[pos].strip():
                try:
                    ex.setdefault("loading", {})["load_lbs"] = float(planned_load_adj[pos])
                except ValueError:
                    pass
            if pos < len(planned_sets_adj) and planned_sets_adj[pos].strip():
                try:
                    ex["sets"] = int(planned_sets_adj[pos])
                except ValueError:
                    pass
            if pos < len(planned_reps_adj) and planned_reps_adj[pos].strip():
                ex["reps"] = planned_reps_adj[pos]
        history[index]["plan_json"] = plan_data

    history[index]["actual_loads"] = actual_loads_dict
    if actual_reps_dict:
        history[index]["actual_reps"] = actual_reps_dict
    if prs:
        history[index]["prs"] = prs

    storage.save_history(profile["client_name"], history, user_id=user["id"])
    return RedirectResponse(url=f"/clients/{slug}/sessions/{index}/complete", status_code=303)


@router.get("/clients/{slug}/sessions/{index}/complete", response_class=HTMLResponse)
def session_complete_view(request: Request, slug: str, index: int, user: dict = Depends(get_current_user)):
    """Show session completion summary with PRs highlighted."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    if index < 0 or index >= len(history):
        return HTMLResponse("<h2>Session not found.</h2>", status_code=404)
    entry = history[index]
    return templates.TemplateResponse("session_complete.html", {
        "request": request,
        "profile": profile,
        "slug": slug,
        "index": index,
        "entry": entry,
        "user": user,
    })


@router.get("/clients/{slug}/sessions/{index}/edit", response_class=HTMLResponse)
def session_plan_edit(request: Request, slug: str, index: int, user: dict = Depends(get_current_user)):
    """Render the plan edit form for a stored session."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result
    if index < 0 or index >= len(history):
        return HTMLResponse("<h2>Session not found.</h2>", status_code=404)
    plan_data = history[index].get("plan_json")
    if plan_data is None:
        return HTMLResponse("<h2>No plan available.</h2>", status_code=404)
    try:
        plan = TrainingSessionPlan.model_validate(plan_data)
    except ValidationError:
        return HTMLResponse("<h2>Plan data is corrupted.</h2>", status_code=500)
    return templates.TemplateResponse("session_edit.html", {
        "request": request,
        "profile": profile,
        "slug": slug,
        "index": index,
        "plan": plan,
        "user": user,
    })


@router.post("/clients/{slug}/sessions/{index}/edit")
def session_plan_edit_save(
    slug: str,
    index: int,
    block_title: Annotated[List[str], Form()] = [],
    block_time: Annotated[List[str], Form()] = [],
    block_format: Annotated[List[str], Form()] = [],
    ex_name: Annotated[List[str], Form()] = [],
    ex_sets: Annotated[List[str], Form()] = [],
    ex_reps: Annotated[List[str], Form()] = [],
    ex_load: Annotated[List[str], Form()] = [],
    ex_tempo: Annotated[List[str], Form()] = [],
    ex_rest: Annotated[List[str], Form()] = [],
    ex_machine_name: Annotated[List[str], Form()] = [],
    ex_seat: Annotated[List[str], Form()] = [],
    ex_lever: Annotated[List[str], Form()] = [],
    ex_pad: Annotated[List[str], Form()] = [],
    ex_machine_notes: Annotated[List[str], Form()] = [],
    ex_cues: Annotated[List[str], Form()] = [],
    ex_regressions: Annotated[List[str], Form()] = [],
    ex_progressions: Annotated[List[str], Form()] = [],
    user: dict = Depends(get_current_user),
):
    """Save trainer edits to a session plan."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    if index < 0 or index >= len(history):
        raise HTTPException(status_code=404, detail="Session not found.")
    plan_data = history[index].get("plan_json", {})

    # Update block-level fields (one entry per block, in order)
    for bi, block in enumerate(plan_data.get("blocks", [])):
        if bi < len(block_title) and block_title[bi].strip():
            block["title"] = block_title[bi].strip()
        if bi < len(block_time) and block_time[bi].strip():
            try:
                block["time_minutes"] = int(block_time[bi])
            except ValueError:
                pass
        if bi < len(block_format):
            block["format"] = block_format[bi].strip() or None

    # Update exercise-level fields (flattened across blocks, in DOM order)
    flat = [
        (bi, ei)
        for bi, blk in enumerate(plan_data.get("blocks", []))
        for ei in range(len(blk.get("exercises", [])))
    ]
    for pos, (bi, ei) in enumerate(flat):
        ex = plan_data["blocks"][bi]["exercises"][ei]
        if pos < len(ex_name) and ex_name[pos].strip():
            ex["name"] = ex_name[pos].strip()
        if pos < len(ex_sets) and ex_sets[pos].strip():
            try:
                ex["sets"] = int(ex_sets[pos])
            except ValueError:
                pass
        if pos < len(ex_reps):
            ex["reps"] = ex_reps[pos].strip() or None
        if pos < len(ex_load) and ex_load[pos].strip():
            try:
                ex.setdefault("loading", {})["load_lbs"] = float(ex_load[pos])
            except ValueError:
                pass
        if pos < len(ex_tempo):
            ex["tempo"] = ex_tempo[pos].strip() or None
        if pos < len(ex_rest) and ex_rest[pos].strip():
            try:
                ex["rest_seconds"] = int(ex_rest[pos])
            except ValueError:
                pass
        ms = ex.get("machine_settings") or {}
        ex["machine_settings"] = ms
        for field, arr in [
            ("machine_name", ex_machine_name),
            ("seat", ex_seat),
            ("lever", ex_lever),
            ("pad", ex_pad),
            ("notes", ex_machine_notes),
        ]:
            if pos < len(arr):
                ms[field] = arr[pos].strip() or None
        if not any(ms.values()):
            ex["machine_settings"] = None
        if pos < len(ex_cues):
            ex["cues"] = [line.strip() for line in ex_cues[pos].splitlines() if line.strip()]
        if pos < len(ex_regressions):
            ex["regressions"] = [line.strip() for line in ex_regressions[pos].splitlines() if line.strip()]
        if pos < len(ex_progressions):
            ex["progressions"] = [line.strip() for line in ex_progressions[pos].splitlines() if line.strip()]

    history[index]["plan_json"] = plan_data
    storage.save_history(profile["client_name"], history, user_id=user["id"])
    storage.append_audit_log(user["id"], "session_plan_edit", f"{slug}:{index}")
    return RedirectResponse(url=f"/clients/{slug}/sessions/{index}", status_code=303)


@router.get("/clients/{slug}/charts", response_class=HTMLResponse)
def progress_charts(request: Request, slug: str, user: dict = Depends(get_current_user)):
    """Load progression charts for the client."""
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, history = result

    # Build per-exercise time series from history (prefer actual_loads over planned loads)
    exercise_data: dict[str, list] = {}
    for entry in history:
        if entry.get("archived"):
            continue
        d = entry.get("session_date", "")
        sn = entry.get("session_number")
        loads = entry.get("actual_loads") or entry.get("loads", {})
        for name, load in loads.items():
            if load is not None:
                exercise_data.setdefault(name, []).append({
                    "date": d,
                    "session": sn,
                    "load": float(load),
                    "is_pr": name in entry.get("prs", {}),
                })

    # Build summary table sorted by PR load descending
    exercises_summary = sorted(
        [
            {
                "name": name,
                "max_load": max(p["load"] for p in points),
                "sessions": len(points),
                "latest": points[-1]["load"] if points else None,
            }
            for name, points in exercise_data.items()
        ],
        key=lambda x: x["max_load"],
        reverse=True,
    )

    return templates.TemplateResponse("progress_charts.html", {
        "request": request,
        "profile": profile,
        "slug": slug,
        "exercise_data_json": json.dumps(exercise_data),
        "exercises_summary": exercises_summary,
        "user": user,
    })


@router.get("/clients/{slug}/goals", response_class=HTMLResponse)
def client_goals_page(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result
    goals = storage.load_goals(profile["client_name"], user_id=user["id"])
    return templates.TemplateResponse("client_goals.html", {
        "request": request,
        "profile": profile,
        "slug": slug,
        "goals": goals,
        "user": user,
    })


@router.get("/clients/{slug}/goals/brainstorm")
@limiter.limit("10/minute")
def goals_brainstorm(request: Request, slug: str, context: str = "", user: dict = Depends(get_current_user)):
    if block := _demo_ai_gate(user, request, json_mode=True):
        return block
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, history = result
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set.")
    suggested = service.brainstorm_goals(
        api_key=api_key,
        client_name=profile["client_name"],
        profile=profile,
        history=history,
        context=context,
    )
    return JSONResponse({"goals": suggested})


@router.post("/clients/{slug}/goals")
def goal_create(
    slug: str,
    text: Annotated[str, Form()],
    category: Annotated[str, Form()] = "other",
    target_date: Annotated[str, Form()] = "",
    milestones_raw: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    goals = storage.load_goals(profile["client_name"], user_id=user["id"])
    goals.append({
        "id": str(int(datetime.now().timestamp() * 1000)),
        "text": text.strip(),
        "category": category,
        "target_date": target_date.strip() or None,
        "status": "active",
        "created": str(date.today()),
        "milestones": [m.strip() for m in milestones_raw.splitlines() if m.strip()],
        "notes": notes.strip(),
    })
    storage.save_goals(profile["client_name"], goals, user_id=user["id"])
    storage.append_audit_log(user["id"], "goal_create", slug)
    return RedirectResponse(url=f"/clients/{slug}/goals", status_code=303)


@router.post("/clients/{slug}/goals/{goal_id}/achieve")
def goal_achieve(slug: str, goal_id: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    goals = storage.load_goals(profile["client_name"], user_id=user["id"])
    for g in goals:
        if g["id"] == goal_id:
            g["status"] = "achieved"
            g["achieved_date"] = str(date.today())
            break
    storage.save_goals(profile["client_name"], goals, user_id=user["id"])
    return RedirectResponse(url=f"/clients/{slug}/goals", status_code=303)


@router.post("/clients/{slug}/goals/{goal_id}/delete")
def goal_delete(slug: str, goal_id: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result
    goals = storage.load_goals(profile["client_name"], user_id=user["id"])
    goals = [g for g in goals if g["id"] != goal_id]
    storage.save_goals(profile["client_name"], goals, user_id=user["id"])
    storage.append_audit_log(user["id"], "goal_delete", f"{slug}:{goal_id}")
    return RedirectResponse(url=f"/clients/{slug}/goals", status_code=303)


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
    dev_mode: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    dev_mode_bool = dev_mode == "on"
    storage.save_trainer_profile(user["id"], {
        "display_name": display_name.strip(),
        "gym_name": gym_name.strip(),
        "contact_info": contact_info.strip(),
        "bio": bio.strip(),
        "dev_mode": dev_mode_bool,
    })
    # Update session immediately so the nav reflects the change without re-login
    user_session = dict(request.session["user"])
    user_session["dev_mode"] = dev_mode_bool
    request.session["user"] = user_session
    return RedirectResponse(url="/profile?saved=1", status_code=303)


@router.get("/export-data")
@limiter.limit("3/hour")
def export_user_data(request: Request, user: dict = Depends(get_current_user)):
    """Download all user data as a ZIP archive (GDPR-style data portability)."""
    user_id = user["id"]
    base = storage._base_dir(user_id)
    trainer_path = storage._TRAINER_DIR / user_id / "profile.json"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if base.exists():
            for path in base.rglob("*.json"):
                zf.write(path, "clients/" + str(path.relative_to(base)))
        if trainer_path.exists():
            zf.write(trainer_path, "trainer/profile.json")
        manifest = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "user_email": user.get("email", ""),
            "format_version": 1,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    filename = f"pt-generator-export-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    storage.append_audit_log(user_id, "data_export", user.get("email", ""))
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse("privacy.html", {"request": request, "user": user})


# ---------------------------------------------------------------------------
# Phase 2: Calendar View
# ---------------------------------------------------------------------------

@router.get("/calendar", response_class=HTMLResponse)
def calendar_view(
    request: Request,
    year: int = 0,
    month: int = 0,
    user: dict = Depends(get_current_user),
):
    """Monthly calendar showing all scheduled sessions across all clients."""
    import calendar as _cal
    today = date.today()
    if not year:
        year = today.year
    if not month:
        month = today.month

    # Clamp to valid range
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    _, days_in_month = _cal.monthrange(year, month)
    start_date = f"{year:04d}-{month:02d}-01"
    end_date = f"{year:04d}-{month:02d}-{days_in_month:02d}"

    sessions = storage.get_sessions_by_date_range(user["id"], start_date, end_date)

    # Build a dict: day_number → list of session info dicts
    days_map: dict[int, list] = {}
    for s in sessions:
        d = int(s["session_date"].split("-")[2])
        days_map.setdefault(d, []).append(s)

    # Build calendar grid: list of weeks, each week is list of (day_number|0)
    first_weekday = _cal.monthrange(year, month)[0]  # 0=Mon
    cal_days: list[list[int]] = []
    week: list[int] = [0] * first_weekday
    for day in range(1, days_in_month + 1):
        week.append(day)
        if len(week) == 7:
            cal_days.append(week)
            week = []
    if week:
        week += [0] * (7 - len(week))
        cal_days.append(week)

    # Prev/next month links
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    import calendar as _cal2
    month_name = _cal2.month_name[month]

    # Pre-build date strings for each day to avoid complex Jinja2 formatting
    date_strs = {d: f"{year:04d}-{month:02d}-{d:02d}" for d in range(1, days_in_month + 1)}

    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "user": user,
        "year": year,
        "month": month,
        "month_name": month_name,
        "cal_days": cal_days,
        "days_map": days_map,
        "date_strs": date_strs,
        "today": today,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    })


# ---------------------------------------------------------------------------
# Phase 3: Progressive Programs
# ---------------------------------------------------------------------------

@router.get("/clients/{slug}/programs", response_class=HTMLResponse)
def programs_list(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result
    programs = storage.list_programs(slug, user["id"])
    return templates.TemplateResponse("programs.html", {
        "request": request,
        "user": user,
        "slug": slug,
        "profile": profile,
        "programs": programs,
    })


@router.get("/clients/{slug}/programs/new", response_class=HTMLResponse)
def program_new_form(request: Request, slug: str, user: dict = Depends(get_current_user)):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result
    return templates.TemplateResponse("program_new.html", {
        "request": request,
        "user": user,
        "slug": slug,
        "profile": profile,
        "today": str(date.today()),
        "error": None,
    })


@router.post("/clients/{slug}/programs/new", response_class=HTMLResponse)
@limiter.limit("5/minute")
def program_new_create(
    request: Request,
    slug: str,
    program_name: Annotated[str, Form()],
    goal_focus: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
    weeks: Annotated[str, Form()] = "4",
    sessions_per_week: Annotated[str, Form()] = "3",
    user: dict = Depends(get_current_user),
):
    if block := _demo_ai_gate(user, request):
        return block

    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result

    program_name = program_name.strip()
    if not program_name:
        return templates.TemplateResponse("program_new.html", {
            "request": request, "user": user, "slug": slug, "profile": profile,
            "today": str(date.today()), "error": "Program name is required.",
        }, status_code=422)

    try:
        w = max(1, min(52, int(weeks)))
        spw = max(1, min(7, int(sessions_per_week)))
    except ValueError:
        return templates.TemplateResponse("program_new.html", {
            "request": request, "user": user, "slug": slug, "profile": profile,
            "today": str(date.today()), "error": "Weeks and sessions/week must be numbers.",
        }, status_code=422)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return templates.TemplateResponse("program_new.html", {
            "request": request, "user": user, "slug": slug, "profile": profile,
            "today": str(date.today()), "error": "ANTHROPIC_API_KEY is not set.",
        }, status_code=500)

    try:
        outline = service.generate_program_outline(
            api_key=api_key,
            client_name=profile["client_name"],
            profile=profile,
            name=program_name,
            goal_focus=goal_focus.strip(),
            weeks=w,
            sessions_per_week=spw,
            start_date=start_date.strip(),
            description=description.strip(),
        )
    except Exception as exc:
        log.exception("Program outline generation failed")
        return templates.TemplateResponse("program_new.html", {
            "request": request, "user": user, "slug": slug, "profile": profile,
            "today": str(date.today()),
            "error": f"Failed to generate program outline: {exc}",
        }, status_code=500)

    program = storage.create_program(
        client_slug=slug,
        user_id=user["id"],
        name=program_name,
        description=description.strip(),
        goal_focus=goal_focus.strip(),
        start_date=start_date.strip(),
        weeks=w,
        sessions_per_week=spw,
        program_json=outline,
    )
    if program is None:
        raise HTTPException(status_code=404, detail="Client not found.")

    storage.append_audit_log(user["id"], "program_create", slug)
    return RedirectResponse(url=f"/clients/{slug}/programs/{program['id']}", status_code=303)


@router.get("/clients/{slug}/programs/{program_id}", response_class=HTMLResponse)
def program_detail(
    request: Request,
    slug: str,
    program_id: int,
    user: dict = Depends(get_current_user),
):
    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        return HTMLResponse("<h2>Client not found.</h2>", status_code=404)
    profile, _ = result

    prog_result = storage.load_program(program_id, user["id"])
    if prog_result is None:
        return HTMLResponse("<h2>Program not found.</h2>", status_code=404)
    program, slots = prog_result

    # Group slots by week for display
    weeks_map: dict[int, list] = {}
    for slot in slots:
        wn = slot["week_number"]
        weeks_map.setdefault(wn, []).append(slot)

    # Pull program_json outline for week themes
    outline_weeks = program.get("program_json", {}).get("weeks", [])
    week_themes = {w.get("week_number", 0): w.get("theme", "") for w in outline_weeks}

    return templates.TemplateResponse("program_detail.html", {
        "request": request,
        "user": user,
        "slug": slug,
        "profile": profile,
        "program": program,
        "weeks_map": weeks_map,
        "week_themes": week_themes,
        "total_slots": len(slots),
        "completed_slots": sum(1 for s in slots if s["session_id"]),
    })


@router.post("/clients/{slug}/programs/{program_id}/generate-session")
def program_generate_session(
    request: Request,
    slug: str,
    program_id: int,
    slot_id: Annotated[int, Form()],
    focus_override: Annotated[str, Form()] = "",
    user: dict = Depends(get_current_user),
):
    """Pre-populate the generate form for a specific program slot."""
    prog_result = storage.load_program(program_id, user["id"])
    if prog_result is None:
        raise HTTPException(status_code=404, detail="Program not found.")
    _, slots = prog_result
    slot = next((s for s in slots if s["id"] == slot_id), None)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found.")

    focus = focus_override.strip() or slot["focus_template"]
    session_date = slot["planned_date"]

    result = storage.load_by_slug(slug, user_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    profile, _ = result

    # Redirect to generate form with pre-filled params + program context in session
    request.session["pending_program_slot"] = {"program_id": program_id, "slot_id": slot_id, "client_slug": slug}
    url = f"/session/new?client={profile['client_name']}&suggested_focus={focus}"
    if session_date:
        url += f"&session_date={session_date}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/clients/{slug}/programs/{program_id}/delete")
def program_delete(
    slug: str,
    program_id: int,
    user: dict = Depends(get_current_user),
):
    storage.delete_program(program_id, user["id"])
    storage.append_audit_log(user["id"], "program_delete", f"{slug}:{program_id}")
    return RedirectResponse(url=f"/clients/{slug}/programs", status_code=303)

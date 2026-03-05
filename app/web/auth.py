import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Header, HTTPException
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app import config
from app import demo_seed as _demo_seed
from app import storage

_DEMO_EMAIL: str = os.environ.get("DEMO_EMAIL", "").lower().strip()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


class UnauthenticatedException(Exception):
    pass


def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise UnauthenticatedException()
    return user


def get_api_user(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Auth dependency for JSON API routes.

    Accepts either a valid session cookie (same as web UI) or an
    ``X-Api-Key`` header matching the ``API_KEY`` environment variable.
    Raises HTTP 401 if neither is present / valid.
    """
    session_user = request.session.get("user")
    if session_user:
        return session_user
    if x_api_key and config.API_KEY and x_api_key == config.API_KEY:
        return {"id": "_api", "email": "api@internal", "name": "API"}
    raise HTTPException(status_code=401, detail="Authentication required.")


router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None})


@router.get("/auth/google")
async def auth_google(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    user_id: str = user_info["sub"]
    email: str = user_info["email"]
    trainer = storage.load_trainer_profile(user_id)
    request.session["user"] = {
        "id": user_id,
        "email": email,
        "name": user_info.get("name", ""),
        "dev_mode": trainer.get("dev_mode", False),
    }
    storage.append_audit_log(user_id, "login", email)
    # Auto-seed demo data on first login for the designated demo account.
    # If seeding actually populates clients, redirect to /clients so the
    # demo viewer immediately sees the full roster.
    if _DEMO_EMAIL and email.lower() == _DEMO_EMAIL:
        if _demo_seed.seed_demo_data(user_id) > 0:
            return RedirectResponse(url="/clients", status_code=302)
    return RedirectResponse(url="/clients", status_code=302)


@router.get("/auth/logout")
async def auth_logout(request: Request):
    user = request.session.get("user")
    if user:
        storage.append_audit_log(user["id"], "logout", user.get("email", ""))
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

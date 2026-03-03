import logging
import os

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.web.api import router as api_router
from app.web.auth import UnauthenticatedException, router as auth_router
from app.web.routes import router as web_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="PT Generator", version="1.0.0")

app.add_middleware(SessionMiddleware, secret_key=os.environ["SECRET_KEY"])

_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
_cors_origins = (
    [f"https://{_railway_domain}"]
    if _railway_domain
    else ["http://localhost:8000", "http://127.0.0.1:8000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(web_router)
app.include_router(api_router)


@app.exception_handler(UnauthenticatedException)
async def unauth_handler(request, exc):
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.web.server:app", host="0.0.0.0", port=8000, reload=True)

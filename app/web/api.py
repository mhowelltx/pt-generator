import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic import ValidationError
from tenacity import RetryError

from app import service
from app.web.auth import get_api_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    client: str = "Sample Client"
    focus: str = "Full-body strength with balance + core integration."
    constraints: Optional[list[str]] = None   # None → use profile defaults
    equipment: Optional[list[str]] = None     # None → use profile defaults
    duration: int = Field(default=50, gt=0)
    session_number: Optional[int] = None
    session_date: Optional[str] = None
    machine_inventory: Optional[list[str]] = None  # None → use profile settings
    export: str = Field(default="none", pattern="^(none|pdf)$")


class GenerateResponse(BaseModel):
    plan: dict
    client_name: Optional[str]
    session_date: Optional[str]
    session_number: Optional[int]
    is_new_client: bool
    prior_session_number: Optional[int]
    prior_session_date: Optional[str]
    export_paths: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, user: dict = Depends(get_api_user)):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(status_code=500, content={"detail": "ANTHROPIC_API_KEY is not set"})

    try:
        plan, ctx = service.run_generation(
            api_key=api_key,
            client=req.client,
            focus=req.focus,
            constraints=req.constraints,
            equipment=req.equipment,
            duration=req.duration,
            session_number=req.session_number,
            session_date=req.session_date,
            machine_inventory=req.machine_inventory,
            user_id=user["id"],
        )
    except ValidationError as exc:
        log.warning("Plan schema validation failed: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"detail": "Generated plan failed schema validation", "errors": exc.errors()},
        )
    except RetryError as exc:
        log.error("Generation failed after retries: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Plan generation failed after all retries. Claude may have returned invalid output."},
        )
    except Exception as exc:
        log.exception("Unexpected generation error")
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    export_paths: dict[str, str] = {}
    if req.export == "pdf":
        from app.export_pdf import export as export_pdf
        export_paths["pdf"] = str(export_pdf(plan))

    return GenerateResponse(
        plan=plan.model_dump(),
        client_name=plan.meta.client_name,
        session_date=plan.meta.session_date,
        session_number=plan.meta.session_number,
        is_new_client=ctx.is_new_client,
        prior_session_number=ctx.prior_session_number,
        prior_session_date=ctx.prior_session_date,
        export_paths=export_paths,
    )

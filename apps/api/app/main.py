"""Ridian Agency — FastAPI entrypoint."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from .services import settings_service
from .services.artifact_service import outputs_dir
from .services.email_delivery_service import send_email
from .services.workflow_service import run_workflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ridian.api")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Ridian Agency API", version="0.1.0")

# Local-only MVP: the Electron renderer makes cross-origin fetches against
# 127.0.0.1:8000. Allow any origin so the desktop GUI, the bundled web
# console, and curl all work. The server only listens on loopback, so this
# is not externally reachable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class WorkflowRequest(BaseModel):
    task: str = Field(..., min_length=10, description="The business task for the agency to handle.")


class WorkflowResponse(BaseModel):
    status: str
    artifact_folder: str
    research_summary: str
    business_document: str
    slide_outline: str
    draft_email: str


class EmailSendRequest(BaseModel):
    subject: str = Field("", description="Subject line. Falls back to a default if empty.")
    body: str = Field(..., min_length=1, description="Email body (plain text).")
    to_email: str | None = Field(None, description="Optional recipient. Defaults to DEFAULT_TO_EMAIL.")


class EmailSendResponse(BaseModel):
    ok: bool
    detail: str
    to_email: str | None = None


class SettingsView(BaseModel):
    """Safe public view of local settings — never contains smtp_password."""
    operator_name: str = ""
    operator_email: str = ""
    default_to_email: str = ""
    company_name: str = ""
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_username: str = ""
    smtp_from_email: str = ""
    smtp_password_configured: bool = False
    outputs_path: str = ""


class SettingsUpdate(BaseModel):
    """All fields optional. Omitted fields are left alone.

    Special case: ``smtp_password`` blank/missing means "keep the existing
    saved value". Any other field present-and-blank clears its value."""
    operator_name: str | None = None
    operator_email: str | None = None
    default_to_email: str | None = None
    company_name: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None


def _settings_view_with_outputs() -> SettingsView:
    s = settings_service.public_view()
    s["outputs_path"] = str(outputs_dir())
    return SettingsView(**s)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "ridian-agency",
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "openai_key_loaded": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.post("/workflows/run", response_model=WorkflowResponse)
async def workflows_run(payload: WorkflowRequest) -> WorkflowResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set.")

    log.info("workflow.start task=%r", payload.task[:120])
    try:
        result = await run_workflow(payload.task)
    except Exception as exc:  # surface SDK errors as 500s with a useful message
        log.exception("workflow.failed")
        raise HTTPException(status_code=500, detail=f"workflow failed: {exc}") from exc

    log.info("workflow.complete folder=%s", result.artifact_folder)
    return WorkflowResponse(
        status="complete",
        artifact_folder=str(result.artifact_folder),
        research_summary=result.research_summary,
        business_document=result.business_document,
        slide_outline=result.slide_outline,
        draft_email=result.draft_email,
    )


@app.get("/settings", response_model=SettingsView)
async def settings_get() -> SettingsView:
    """Return the operator-visible settings. Never includes smtp_password."""
    return _settings_view_with_outputs()


@app.post("/settings", response_model=SettingsView)
async def settings_post(payload: SettingsUpdate) -> SettingsView:
    """Persist settings to apps/api/local_settings.json.

    Blank ``smtp_password`` means "keep the previous value" so the renderer
    never has to round-trip the password back to the server."""
    updates = payload.model_dump(exclude_unset=False)
    settings_service.save_settings(updates)
    return _settings_view_with_outputs()


@app.post("/email/send-approved", response_model=EmailSendResponse)
async def email_send_approved(payload: EmailSendRequest) -> EmailSendResponse:
    """Send a draft email — only after the operator clicked Approve in the GUI.

    The endpoint never auto-sends; it requires an explicit POST initiated by
    the user. SMTP credentials are read from env vars by the service and
    never returned to the client. If SMTP is unconfigured the response is a
    503 with a clear, actionable message.
    """
    log.info("email.send_approved.start subject=%r len_body=%d to=%s",
             (payload.subject or "")[:80], len(payload.body or ""),
             payload.to_email or "<default>")
    result = send_email(payload.subject, payload.body, payload.to_email)
    if not result.ok:
        log.info("email.send_approved.failed detail=%s", result.detail)
        raise HTTPException(status_code=503, detail=result.detail)
    log.info("email.send_approved.ok to=%s", result.to_email)
    return EmailSendResponse(ok=True, detail=result.detail, to_email=result.to_email)

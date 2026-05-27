"""Ridian Agency — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
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

# Mirror any saved OPENAI_* settings into os.environ BEFORE importing the
# workflow / agents modules. The Agent instances read OPENAI_MODEL at import
# time via default_model(); the OpenAI SDK reads OPENAI_API_KEY at request
# time. This call ensures both see the user's saved values from
# apps/api/local_settings.json on a fresh start.
from .services import settings_service  # noqa: E402  (import order matters)

settings_service.apply_to_environment()

from .services.artifact_service import outputs_dir  # noqa: E402
from .services.email_delivery_service import send_email  # noqa: E402
from .services.export_service import (  # noqa: E402
    ExportError,
    export_docx,
    export_pptx,
    export_zip,
    open_artifact_file,
    open_artifact_folder,
)
from .services import google_drive_service  # noqa: E402
from .services import project_service  # noqa: E402
from .services.social_media_workflow_service import (  # noqa: E402
    SocialMediaInput,
    run_social_media_workflow,
)
from .services.workflow_service import run_workflow  # noqa: E402

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
    """Safe public view of local settings — never contains secrets.

    ``smtp_password`` and ``openai_api_key`` are exposed only as
    ``*_configured: bool`` flags."""
    operator_name: str = ""
    operator_email: str = ""
    default_to_email: str = ""
    company_name: str = ""
    openai_model: str = ""
    openai_api_key_configured: bool = False
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_username: str = ""
    smtp_from_email: str = ""
    smtp_password_configured: bool = False
    google_drive_root_folder_id: str = ""
    outputs_path: str = ""


class SettingsUpdate(BaseModel):
    """All fields optional. Omitted fields are left alone.

    Special case: secrets (``smtp_password``, ``openai_api_key``) blank or
    missing means "keep the existing saved value". Any other field present
    and blank clears that field."""
    operator_name: str | None = None
    operator_email: str | None = None
    default_to_email: str | None = None
    company_name: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    google_drive_root_folder_id: str | None = None


class ArtifactFolderRequest(BaseModel):
    artifact_folder: str = Field(..., min_length=1)


class ArtifactFileRequest(BaseModel):
    artifact_folder: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)


class ArtifactOpenResponse(BaseModel):
    status: str
    detail: str
    path: str


class ArtifactZipResponse(BaseModel):
    status: str
    zip_path: str


class ArtifactDocxResponse(BaseModel):
    status: str
    docx_path: str


class ArtifactPptxResponse(BaseModel):
    status: str
    pptx_path: str


class GoogleStatusResponse(BaseModel):
    """Safe public Google Drive connection state. Never contains tokens."""
    connected: bool
    email: str | None = None


class GoogleUploadRequest(BaseModel):
    artifact_folder: str = Field(..., min_length=1)


class GoogleUploadResponse(BaseModel):
    status: str
    drive_folder_name: str
    drive_folder_url: str
    drive_path: str = ""  # "Ridian Technologies / Ridian Agency / ... / <run>"
    uploaded_files: list[str]


class SocialMediaRequest(BaseModel):
    channel: str = Field(..., min_length=1)
    starting_point: str = Field("", description="What the operator has on hand.")
    content_format: str = Field("", description="Short-form / long-form / etc.")
    media_notes: str = Field("", description="Description of existing footage or thumbnail.")
    topic_notes: str = Field("", description="Topic, rough notes, transcript, or concept.")
    goal: str = Field("", description="Educate / entertain / drive traffic / etc.")
    output_depth: str = Field("", description="Quick / full / weekly plan.")
    image_data: str | None = Field(None, description="Base64 data URI of an optional thumbnail/image.")


class SocialMediaResponse(BaseModel):
    status: str
    artifact_folder: str
    content_package: str
    script: str
    caption_package: str
    posting_checklist: str


class RecentProject(BaseModel):
    artifact_folder: str
    name: str
    workflow: str
    channel: str = ""
    mtime_iso: str
    pinned: bool = False


class RecentProjectsResponse(BaseModel):
    projects: list[RecentProject]


class ProjectFolderRequest(BaseModel):
    artifact_folder: str = Field(..., min_length=1)


class ProjectActionResponse(BaseModel):
    ok: bool
    name: str
    # Both hide/unhide and pin/unpin reuse this response model; the
    # field that's not relevant for a given call is omitted (default 0).
    hidden_count: int = 0
    pinned_count: int = 0


class LoadProjectResponse(BaseModel):
    """Compatible with both workflow response shapes — renderer reads
    whichever fields are present for the detected workflow type."""
    artifact_folder: str
    name: str
    workflow: str
    channel: str = ""
    task: str = ""
    # Business
    research_summary: str = ""
    business_document: str = ""
    slide_outline: str = ""
    draft_email: str = ""
    # Social
    content_package: str = ""
    script: str = ""
    caption_package: str = ""
    posting_checklist: str = ""


def _settings_view_with_outputs() -> SettingsView:
    s = settings_service.public_view()
    s["outputs_path"] = str(outputs_dir())
    return SettingsView(**s)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    # Reads through settings_service so the saved local_settings.json takes
    # precedence over .env. Never returns the key itself.
    return {
        "status": "ok",
        "service": "ridian-agency",
        "model": settings_service.get_effective_value("OPENAI_MODEL") or "gpt-4o-mini",
        "openai_key_loaded": bool(settings_service.get_effective_value("OPENAI_API_KEY")),
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


@app.post("/workflows/social-media/run", response_model=SocialMediaResponse)
async def workflows_social_media_run(payload: SocialMediaRequest) -> SocialMediaResponse:
    """Run the Social Media Production workflow.

    Produces a four-section package (Content / Script / Caption / Posting
    Checklist) saved to ``outputs/<timestamp>_<slug>/``. The agent reads
    OPENAI_API_KEY and OPENAI_MODEL via settings_service the same way the
    business workflow does.
    """
    if not settings_service.get_effective_value("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not set. Open Settings to add your key.",
        )

    log.info("social_workflow.start channel=%r format=%r depth=%r",
             payload.channel, payload.content_format, payload.output_depth)

    try:
        result = await run_social_media_workflow(
            SocialMediaInput(
                channel=payload.channel,
                starting_point=payload.starting_point,
                content_format=payload.content_format,
                media_notes=payload.media_notes,
                topic_notes=payload.topic_notes,
                goal=payload.goal,
                output_depth=payload.output_depth,
                image_data=payload.image_data,
            )
        )
    except Exception as exc:
        log.exception("social_workflow.failed")
        raise HTTPException(status_code=500, detail=f"social workflow failed: {exc}") from exc

    log.info("social_workflow.complete folder=%s", result.artifact_folder)
    return SocialMediaResponse(
        status="complete",
        artifact_folder=str(result.artifact_folder),
        content_package=result.content_package,
        script=result.script,
        caption_package=result.caption_package,
        posting_checklist=result.posting_checklist,
    )


# ---------------------------------------------------------------------------
# Recent projects — sidebar listing + reopen-without-rerun
# ---------------------------------------------------------------------------


@app.get("/projects/recent", response_model=RecentProjectsResponse)
async def projects_recent(limit: int = 30, include_hidden: bool = False) -> RecentProjectsResponse:
    """List recent run folders. Hidden runs filtered out by default."""
    items = project_service.list_recent_projects(limit=limit, include_hidden=include_hidden)
    return RecentProjectsResponse(projects=[RecentProject(**i) for i in items])


@app.get("/projects/hidden", response_model=RecentProjectsResponse)
async def projects_hidden() -> RecentProjectsResponse:
    """List the run folders the user has hidden from the sidebar.

    Folders are not deleted by hiding — this just returns whichever
    on-disk runs are currently marked hidden so the GUI can offer to
    restore them.
    """
    items = project_service.list_hidden_projects()
    return RecentProjectsResponse(projects=[RecentProject(**i) for i in items])


@app.post("/projects/hide", response_model=ProjectActionResponse)
async def projects_hide(payload: ProjectFolderRequest) -> ProjectActionResponse:
    """Mark a run folder as hidden in the sidebar. Folder stays on disk."""
    try:
        data = project_service.hide_project(payload.artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ProjectActionResponse(**data)


@app.post("/projects/unhide", response_model=ProjectActionResponse)
async def projects_unhide(payload: ProjectFolderRequest) -> ProjectActionResponse:
    """Remove a run folder from the hidden list so it shows up again."""
    try:
        data = project_service.unhide_project(payload.artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ProjectActionResponse(**data)


@app.post("/projects/pin", response_model=ProjectActionResponse)
async def projects_pin(payload: ProjectFolderRequest) -> ProjectActionResponse:
    """Pin a run folder so it sorts to the top of Recent runs.

    Mutually exclusive with hide: pinning a hidden folder unhides it."""
    try:
        data = project_service.pin_project(payload.artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ProjectActionResponse(**data)


@app.post("/projects/unpin", response_model=ProjectActionResponse)
async def projects_unpin(payload: ProjectFolderRequest) -> ProjectActionResponse:
    """Remove a run folder from the pinned list."""
    try:
        data = project_service.unpin_project(payload.artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ProjectActionResponse(**data)


@app.get("/projects/load", response_model=LoadProjectResponse)
async def projects_load(artifact_folder: str) -> LoadProjectResponse:
    """Reopen a prior run: read its allowlisted Markdown files back into a
    response the renderer can render without re-calling the model."""
    try:
        data = project_service.load_project(artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return LoadProjectResponse(**data)


@app.get("/settings", response_model=SettingsView)
async def settings_get() -> SettingsView:
    """Return the operator-visible settings. Never includes smtp_password."""
    return _settings_view_with_outputs()


@app.post("/settings", response_model=SettingsView)
async def settings_post(payload: SettingsUpdate) -> SettingsView:
    """Persist settings to apps/api/local_settings.json.

    Blank ``smtp_password`` means "keep the previous value" so the renderer
    never has to round-trip the password back to the server."""
    # exclude_unset=True so a client that omits a field leaves the saved value
    # alone. Sending a present-but-empty string still clears the field (except
    # for secrets — smtp_password and openai_api_key have a preserve-on-blank
    # rule in the service).
    updates = payload.model_dump(exclude_unset=True)
    settings_service.save_settings(updates)
    # Mirror updated OPENAI_* values into os.environ so the next workflow run
    # (and the next /health probe) see them without a backend restart.
    settings_service.apply_to_environment()
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


# ---------------------------------------------------------------------------
# Artifact actions (open + export). All endpoints validate the folder path
# is inside the configured outputs/ directory and reject anything else.
# Filenames for open-file are restricted to an allowlist in export_service.
# ---------------------------------------------------------------------------


def _export_error_to_http(exc: ExportError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@app.post("/artifacts/open-folder", response_model=ArtifactOpenResponse)
async def artifacts_open_folder(payload: ArtifactFolderRequest) -> ArtifactOpenResponse:
    try:
        folder = open_artifact_folder(payload.artifact_folder)
    except ExportError as exc:
        raise _export_error_to_http(exc) from exc
    return ArtifactOpenResponse(status="success", detail="Folder opened.", path=str(folder))


@app.post("/artifacts/open-file", response_model=ArtifactOpenResponse)
async def artifacts_open_file(payload: ArtifactFileRequest) -> ArtifactOpenResponse:
    try:
        path = open_artifact_file(payload.artifact_folder, payload.filename)
    except ExportError as exc:
        raise _export_error_to_http(exc) from exc
    return ArtifactOpenResponse(status="success", detail="File opened.", path=str(path))


@app.post("/artifacts/export-zip", response_model=ArtifactZipResponse)
async def artifacts_export_zip(payload: ArtifactFolderRequest) -> ArtifactZipResponse:
    try:
        zip_path = export_zip(payload.artifact_folder)
    except ExportError as exc:
        raise _export_error_to_http(exc) from exc
    return ArtifactZipResponse(status="success", zip_path=str(zip_path))


@app.post("/artifacts/export-docx", response_model=ArtifactDocxResponse)
async def artifacts_export_docx(payload: ArtifactFolderRequest) -> ArtifactDocxResponse:
    try:
        docx_path = export_docx(payload.artifact_folder)
    except ExportError as exc:
        raise _export_error_to_http(exc) from exc
    return ArtifactDocxResponse(status="success", docx_path=str(docx_path))


@app.post("/artifacts/export-pptx", response_model=ArtifactPptxResponse)
async def artifacts_export_pptx(payload: ArtifactFolderRequest) -> ArtifactPptxResponse:
    try:
        pptx_path = export_pptx(payload.artifact_folder)
    except ExportError as exc:
        raise _export_error_to_http(exc) from exc
    return ArtifactPptxResponse(status="success", pptx_path=str(pptx_path))


# ---------------------------------------------------------------------------
# Google Drive (approval-only). drive.file scope — only files the app makes.
# No endpoint ever returns OAuth tokens or the client_secret. The renderer
# only ever sees connected state + email + folder URL.
# ---------------------------------------------------------------------------


def _google_error_to_http(exc: google_drive_service.GoogleDriveError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@app.get("/google/status", response_model=GoogleStatusResponse)
async def google_status() -> GoogleStatusResponse:
    return GoogleStatusResponse(**google_drive_service.get_status())


@app.post("/google/connect", response_model=GoogleStatusResponse)
async def google_connect() -> GoogleStatusResponse:
    """Run the installed-app OAuth flow.

    Blocks the calling HTTP request until the user finishes consent in
    their browser. Runs via asyncio.to_thread so uvicorn stays responsive
    to /health, /google/status, and other endpoints during the wait.
    """
    try:
        status = await asyncio.to_thread(google_drive_service.run_oauth_flow)
    except google_drive_service.GoogleDriveError as exc:
        raise _google_error_to_http(exc) from exc
    return GoogleStatusResponse(**status)


@app.post("/google/disconnect", response_model=GoogleStatusResponse)
async def google_disconnect() -> GoogleStatusResponse:
    return GoogleStatusResponse(**google_drive_service.disconnect())


@app.post("/google/upload-artifacts", response_model=GoogleUploadResponse)
async def google_upload_artifacts(payload: GoogleUploadRequest) -> GoogleUploadResponse:
    """Upload allowlisted artifact files to a new Drive folder.

    Requires an already-connected Google Drive account. Validates that the
    artifact folder lives inside the configured outputs/ directory; rejects
    path traversal and arbitrary paths.
    """
    try:
        result = await asyncio.to_thread(
            google_drive_service.upload_artifact_folder, payload.artifact_folder
        )
    except google_drive_service.GoogleDriveError as exc:
        raise _google_error_to_http(exc) from exc
    return GoogleUploadResponse(**result)

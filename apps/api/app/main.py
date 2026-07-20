"""Ridian Agency — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
from .services import dashboard_service  # noqa: E402
from .services import google_drive_service  # noqa: E402
from .services import memory_service  # noqa: E402
from .services import operation_log_service  # noqa: E402
from .services import operator_service  # noqa: E402
from .services import pdf_service  # noqa: E402
from .services import project_service  # noqa: E402
from .services import transcription_service  # noqa: E402
from .services.agentic_advances_workflow_service import (  # noqa: E402
    ALLOWED_OUTPUT_DEPTHS as AGENTIC_DEPTHS,
    ALLOWED_TIME_WINDOWS as AGENTIC_WINDOWS,
    AgenticAdvancesInput,
    run_agentic_advances_workflow,
)
from .services.notebooklm_workflow_service import (  # noqa: E402
    ALLOWED_AUDIENCES as NLM_AUDIENCES,
    ALLOWED_OUTPUT_TYPES as NLM_OUTPUT_TYPES,
    ALLOWED_PURPOSES as NLM_PURPOSES,
    NotebookLMInput,
    run_notebooklm_workflow,
)
from .services.social_media_workflow_service import (  # noqa: E402
    SocialMediaInput,
    run_social_media_workflow,
)
from .services.workflow_service import run_workflow  # noqa: E402

# Console + rotating file: run forensics (e.g. anthropic.web_search
# searches=N) must survive the uvicorn console. state/ is git-ignored.
from .services.state_store import STATE_DIR  # noqa: E402

_LOG_DIR = STATE_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "backend.log", maxBytes=2_000_000, backupCount=3,
            encoding="utf-8",
        ),
    ],
)
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


class RootFolderValidation(BaseModel):
    """Structured Drive-folder validation result. Always safe to serialize:
    never carries tokens, raw API responses, or the folder body itself."""
    ok: bool
    blank: bool = False
    folder_id: str = ""
    folder_name: str | None = None
    detail: str = ""
    reason: str = ""


class SettingsView(BaseModel):
    """Safe public view of local settings — never contains secrets.

    ``smtp_password`` and ``openai_api_key`` are exposed only as
    ``*_configured: bool`` flags."""
    operator_name: str = ""
    operator_email: str = ""
    default_to_email: str = ""
    company_name: str = ""
    anthropic_model: str = ""
    anthropic_api_key_configured: bool = False
    openai_model: str = ""
    openai_api_key_configured: bool = False
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_username: str = ""
    smtp_from_email: str = ""
    smtp_password_configured: bool = False
    google_drive_root_folder_id: str = ""
    # v1.4: when True (default), every operator run auto-uploads its artifact
    # folder to Google Drive at the end of the run, no manual click required.
    # Stored as "true"/"false" string in local_settings.json.
    operator_auto_upload_drive: str = "true"
    appearance: str = ""
    outputs_path: str = ""
    # Populated on /settings POST when a non-blank root folder ID is saved
    # so the renderer can show a clear, actionable warning if the configured
    # folder is inaccessible. None on GET — the renderer calls
    # /google/validate-root-folder for an on-demand check.
    root_folder_validation: RootFolderValidation | None = None


class SettingsUpdate(BaseModel):
    """All fields optional. Omitted fields are left alone.

    Special case: secrets (``smtp_password``, ``openai_api_key``) blank or
    missing means "keep the existing saved value". Any other field present
    and blank clears that field."""
    operator_name: str | None = None
    operator_email: str | None = None
    default_to_email: str | None = None
    company_name: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    google_drive_root_folder_id: str | None = None
    operator_auto_upload_drive: str | None = None
    appearance: str | None = None


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
    visual_production: str = ""


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
    """Compatible with every workflow response shape — renderer reads
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
    visual_production: str = ""
    # Agentic Advances
    agentic_advances_brief: str = ""
    # NotebookLM
    notebooklm_package: str = ""


class AgenticAdvancesRequest(BaseModel):
    topic_focus: str = Field("", description="Optional Ridian-specific topic focus.")
    time_window: str = Field("Last 7 days", description="Look-back window for web research.")
    output_depth: str = Field("Strategic brief", description="Quick / Strategic / Deep research brief.")


class AgenticAdvancesResponse(BaseModel):
    status: str
    artifact_folder: str
    agentic_advances_brief: str


class NotebookLMRequest(BaseModel):
    subject: str = Field(..., min_length=3, description="Subject / topic for the NotebookLM package.")
    purpose: str = Field("Learn", description="Learn / Strategy / Content / Planning / Teaching.")
    audience: str = Field("Ryan", description="Intended audience for the package.")
    output_type: str = Field("Full NotebookLM package", description="Source prompt / Audio Overview / Full.")
    notes: str = Field("", description="Optional notes or context.")


class NotebookLMResponse(BaseModel):
    status: str
    artifact_folder: str
    notebooklm_package: str


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
        "model": settings_service.get_effective_value("ANTHROPIC_MODEL") or "claude-opus-4-8",
        "anthropic_key_loaded": bool(settings_service.get_effective_value("ANTHROPIC_API_KEY")),
        "openai_key_loaded": bool(settings_service.get_effective_value("OPENAI_API_KEY")),
    }


@app.post("/workflows/run", response_model=WorkflowResponse)
async def workflows_run(payload: WorkflowRequest) -> WorkflowResponse:
    if not settings_service.get_effective_value("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set.")

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
    if not settings_service.get_effective_value("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set. Open Settings to add your Anthropic API key.",
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
        visual_production=result.visual_production,
    )


@app.post("/workflows/agentic-advances/run", response_model=AgenticAdvancesResponse)
async def workflows_agentic_advances_run(payload: AgenticAdvancesRequest) -> AgenticAdvancesResponse:
    """Run the Agentic Advances Daily Brief workflow.

    Uses the OpenAI Agents SDK hosted ``WebSearchTool`` to ground the brief
    in current sources. The artifact is a single Markdown file:
    ``agentic_advances_brief.md``.
    """
    if not settings_service.get_effective_value("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set. Open Settings to add your Anthropic API key.",
        )

    if payload.time_window and payload.time_window not in AGENTIC_WINDOWS:
        raise HTTPException(status_code=400, detail=f"time_window must be one of {list(AGENTIC_WINDOWS)}")
    if payload.output_depth and payload.output_depth not in AGENTIC_DEPTHS:
        raise HTTPException(status_code=400, detail=f"output_depth must be one of {list(AGENTIC_DEPTHS)}")

    log.info("agentic.start window=%r depth=%r focus=%r",
             payload.time_window, payload.output_depth, payload.topic_focus[:80])

    try:
        result = await run_agentic_advances_workflow(
            AgenticAdvancesInput(
                topic_focus=payload.topic_focus,
                time_window=payload.time_window,
                output_depth=payload.output_depth,
            )
        )
    except Exception as exc:
        log.exception("agentic.failed")
        raise HTTPException(status_code=500, detail=f"agentic advances workflow failed: {exc}") from exc

    log.info("agentic.complete folder=%s", result.artifact_folder)
    return AgenticAdvancesResponse(
        status="complete",
        artifact_folder=str(result.artifact_folder),
        agentic_advances_brief=result.agentic_advances_brief,
    )


@app.post("/workflows/notebooklm/run", response_model=NotebookLMResponse)
async def workflows_notebooklm_run(payload: NotebookLMRequest) -> NotebookLMResponse:
    """Run the NotebookLM Prompt + Audio Overview Builder workflow.

    Produces a single Markdown artifact, ``notebooklm_package.md``, with
    a copy-paste-ready Audio Overview prompt and supporting prompts.
    """
    if not settings_service.get_effective_value("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set. Open Settings to add your Anthropic API key.",
        )

    if payload.purpose and payload.purpose not in NLM_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of {list(NLM_PURPOSES)}")
    if payload.audience and payload.audience not in NLM_AUDIENCES:
        raise HTTPException(status_code=400, detail=f"audience must be one of {list(NLM_AUDIENCES)}")
    if payload.output_type and payload.output_type not in NLM_OUTPUT_TYPES:
        raise HTTPException(status_code=400, detail=f"output_type must be one of {list(NLM_OUTPUT_TYPES)}")

    log.info("notebooklm.start subject=%r purpose=%r audience=%r type=%r",
             payload.subject[:80], payload.purpose, payload.audience, payload.output_type)

    try:
        result = await run_notebooklm_workflow(
            NotebookLMInput(
                subject=payload.subject,
                purpose=payload.purpose,
                audience=payload.audience,
                output_type=payload.output_type,
                notes=payload.notes,
            )
        )
    except Exception as exc:
        log.exception("notebooklm.failed")
        raise HTTPException(status_code=500, detail=f"notebooklm workflow failed: {exc}") from exc

    log.info("notebooklm.complete folder=%s", result.artifact_folder)
    return NotebookLMResponse(
        status="complete",
        artifact_folder=str(result.artifact_folder),
        notebooklm_package=result.notebooklm_package,
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
    never has to round-trip the password back to the server.

    For ``google_drive_root_folder_id``: we normalize whatever the operator
    pasted (full URL, raw ID, with/without scheme, trailing slash/query) into
    a bare folder ID before persisting. After save, if a non-blank folder ID
    landed, we probe Drive to check whether the current token can access it
    and attach the result to the response so the renderer can show a clear
    warning inline. We never block the save — the operator keeps their input
    and gets an actionable message about whether uploads will work.
    """
    updates = payload.model_dump(exclude_unset=True)

    # Pre-save normalize: store the bare folder ID, not the URL the user pasted.
    if "google_drive_root_folder_id" in updates:
        raw = updates["google_drive_root_folder_id"] or ""
        normalized = google_drive_service._normalize_root_folder_id(raw)
        # If the user pasted SOMETHING but normalization yielded nothing
        # (garbage / typo), we still persist empty so the app falls back to
        # the safe app-folder mode rather than carrying an unusable value.
        # The validation block below will surface a clear "that didn't look
        # like a folder ID" message.
        updates["google_drive_root_folder_id"] = normalized if normalized else (
            "" if raw.strip() == "" else ""  # explicit: clear on garbage input
        )
        # Remember the original (stripped) input so the validation message can
        # tell the difference between "blank" and "we couldn't parse what you
        # pasted." We don't persist this; it's local to this request.
        raw_present = bool(raw.strip())
    else:
        raw_present = False

    settings_service.save_settings(updates)
    settings_service.apply_to_environment()

    view = _settings_view_with_outputs()

    # Post-save validation — only when the operator actually touched the field.
    if "google_drive_root_folder_id" in updates:
        result = await asyncio.to_thread(
            google_drive_service.validate_root_folder_access,
            updates["google_drive_root_folder_id"],
        )
        # Edge: user pasted garbage that didn't normalize. Surface that
        # explicitly even though we persisted "" so the warning isn't lost.
        if raw_present and not updates["google_drive_root_folder_id"]:
            result = {
                "ok": False, "blank": False, "folder_id": "", "folder_name": None,
                "detail": (
                    "That didn't look like a Google Drive folder ID or URL — "
                    "the field has been cleared. Paste a URL from your browser's "
                    "address bar while viewing the folder, or leave blank to let "
                    "Ridian create its own app folder."
                ),
                "reason": "invalid_id",
            }
        view.root_folder_validation = RootFolderValidation(**result)

    return view


@app.get("/google/validate-root-folder", response_model=RootFolderValidation)
async def google_validate_root_folder(folder_id_or_url: str = "") -> RootFolderValidation:
    """On-demand validation for the Settings 'Test folder access' button.

    Does not write to settings. Useful before saving so the operator can paste
    a URL and see whether Ridian can access it.
    """
    result = await asyncio.to_thread(
        google_drive_service.validate_root_folder_access, folder_id_or_url,
    )
    return RootFolderValidation(**result)


# ---------------------------------------------------------------------------
# Memory + Dashboard (Ridian Command Center)
# ---------------------------------------------------------------------------


class ContactPayload(BaseModel):
    name: str = ""
    role: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    notes: str = ""
    last_contact_iso: str = ""


class FactPayload(BaseModel):
    topic: str = ""
    fact: str = Field(..., min_length=1)
    source: str = ""


class FollowUpPayload(BaseModel):
    what: str = Field(..., min_length=1)
    who: str = ""
    due_iso: str = ""
    status: str = "open"
    source_run: str = ""


class FollowUpUpdate(BaseModel):
    what: str | None = None
    who: str | None = None
    due_iso: str | None = None
    status: str | None = None
    source_run: str | None = None


class DecisionPayload(BaseModel):
    decision: str = Field(..., min_length=1)
    context: str = ""
    date_iso: str = ""


class BrandSectionPayload(BaseModel):
    voice: str = ""
    audience: str = ""
    do: list[str] = []
    avoid: list[str] = []
    notes: str = ""


class BrandPayload(BaseModel):
    ridian: BrandSectionPayload | None = None
    open_gulf: BrandSectionPayload | None = None
    buns: BrandSectionPayload | None = None


@app.get("/memory/summary")
async def memory_summary_get() -> dict:
    return memory_service.memory_summary()


@app.get("/memory/contacts")
async def memory_contacts_list() -> dict:
    return {"contacts": memory_service.list_contacts()}


@app.post("/memory/contacts")
async def memory_contacts_create(payload: ContactPayload) -> dict:
    if not payload.name.strip() and not payload.email.strip():
        raise HTTPException(status_code=400, detail="name or email required")
    return memory_service.add_contact(payload.model_dump(), written_by="manual")


@app.put("/memory/contacts/{contact_id}")
async def memory_contacts_update(contact_id: str, payload: ContactPayload) -> dict:
    updated = memory_service.update_contact(contact_id, payload.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return updated


@app.delete("/memory/contacts/{contact_id}")
async def memory_contacts_delete(contact_id: str) -> dict:
    if not memory_service.delete_contact(contact_id):
        raise HTTPException(status_code=404, detail="contact not found")
    return {"status": "deleted", "id": contact_id}


@app.get("/memory/facts")
async def memory_facts_list() -> dict:
    return {"facts": memory_service.list_facts()}


@app.post("/memory/facts")
async def memory_facts_create(payload: FactPayload) -> dict:
    return memory_service.add_fact(payload.model_dump(), written_by="manual")


@app.delete("/memory/facts/{fact_id}")
async def memory_facts_delete(fact_id: str) -> dict:
    if not memory_service.delete_fact(fact_id):
        raise HTTPException(status_code=404, detail="fact not found")
    return {"status": "deleted", "id": fact_id}


@app.get("/memory/follow-ups")
async def memory_follow_ups_list() -> dict:
    return {"follow_ups": memory_service.list_follow_ups()}


@app.post("/memory/follow-ups")
async def memory_follow_ups_create(payload: FollowUpPayload) -> dict:
    return memory_service.add_follow_up(payload.model_dump(), written_by="manual")


@app.put("/memory/follow-ups/{follow_up_id}")
async def memory_follow_ups_update(follow_up_id: str, payload: FollowUpUpdate) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    updated = memory_service.update_follow_up(follow_up_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="follow-up not found")
    return updated


@app.delete("/memory/follow-ups/{follow_up_id}")
async def memory_follow_ups_delete(follow_up_id: str) -> dict:
    if not memory_service.delete_follow_up(follow_up_id):
        raise HTTPException(status_code=404, detail="follow-up not found")
    return {"status": "deleted", "id": follow_up_id}


@app.get("/memory/decisions")
async def memory_decisions_list() -> dict:
    return {"decisions": memory_service.list_decisions()}


@app.post("/memory/decisions")
async def memory_decisions_create(payload: DecisionPayload) -> dict:
    return memory_service.add_decision(payload.model_dump(), written_by="manual")


@app.delete("/memory/decisions/{decision_id}")
async def memory_decisions_delete(decision_id: str) -> dict:
    if not memory_service.delete_decision(decision_id):
        raise HTTPException(status_code=404, detail="decision not found")
    return {"status": "deleted", "id": decision_id}


@app.get("/memory/brand")
async def memory_brand_get() -> dict:
    return memory_service.get_brand()


@app.post("/memory/brand")
async def memory_brand_save(payload: BrandPayload) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    return memory_service.save_brand(updates)


class ProfilePayload(BaseModel):
    """Operator Profile — the business context that grounds every operation."""
    operator: str = ""
    business: str = ""
    offerings: str = ""
    customers: str = ""
    goal: str = ""
    avoid: str = ""
    notes: str = ""


@app.get("/memory/profile")
async def memory_profile_get() -> dict:
    return memory_service.get_profile()


@app.post("/memory/profile")
async def memory_profile_save(payload: ProfilePayload) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    return memory_service.save_profile(updates)


@app.get("/dashboard")
async def dashboard_get() -> dict:
    return dashboard_service.build_dashboard()


# ---------------------------------------------------------------------------
# Operator v1 — natural-command operations + live SSE timeline
# ---------------------------------------------------------------------------


class OperationRunRequest(BaseModel):
    command: str = Field(..., min_length=4, description="Natural-language command for Ridian to execute.")
    project_id: str = Field("", description="Optional operator project to file this run under.")
    # v3.6: background (fire-and-forget) run. SAFE-ONLY contract: the flag
    # never bypasses a gate — it only ADDS one (save_memory refuses unattended
    # writes) and registers the run for completion/parked notifications.
    background: bool = Field(False, description="Run detached: safe work only; parks at any approval gate.")
    research_model: str = Field("", description="Optional per-run research-model override (allowlisted server-side).")
    script_model: str = Field("", description="Optional per-run script-writer model override (allowlisted server-side).")
    effort: str = Field("", description="Optional per-run sub-agent effort level: low|medium|high (allowlisted server-side).")


# Comment-line heartbeat cadence for operation SSE streams. Long tool calls
# (live web research) can go minutes without emitting an event; a stream that
# sends zero bytes for that long gets killed by idle-socket policies between
# Electron and uvicorn, and the renderer then reports a healthy run as Failed.
# SSE comment lines (": ...") are protocol-legal keep-alives the renderer
# already skips.
_SSE_HEARTBEAT_SECONDS = 15.0

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _operation_sse(run) -> StreamingResponse:
    """Queue-backed SSE stream for one operation run.

    ``run`` is an async callable taking the emit function. The runner task is
    detached from the HTTP request, so its exceptions would otherwise die
    unlogged on stderr — they are logged here, and the stream always closes
    with an ``end`` event.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await run(emit)
        except Exception:  # noqa: BLE001 — persist the traceback, never re-raise
            log.exception("operation runner failed")
        finally:
            await queue.put(None)  # sentinel — stop the stream

    asyncio.create_task(runner())

    async def event_stream():
        # Initial comment line so the client immediately knows the stream is live.
        yield ": connected\n\n"
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if evt is None:
                yield "event: end\ndata: {}\n\n"
                break
            try:
                payload_json = json.dumps(evt.get("data", {}), default=str)
            except (TypeError, ValueError):
                payload_json = json.dumps({"raw": str(evt)})
            yield f"event: {evt.get('event', 'message')}\ndata: {payload_json}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=dict(_SSE_HEADERS))


@app.post("/operations/run")
async def operations_run(payload: OperationRunRequest) -> StreamingResponse:
    """Run an operation and stream timeline events as Server-Sent Events.

    The renderer subscribes via fetch + ReadableStream (or EventSource).
    Each event is a JSON object on a single ``data:`` line, terminated by
    a blank line per SSE spec.
    """
    return _operation_sse(lambda emit: operator_service.run_operation(
        command=payload.command, emit=emit, project_id=payload.project_id,
        research_model=payload.research_model, script_model=payload.script_model,
        effort=payload.effort, background=payload.background,
    ))


@app.post("/operations/{operation_id}/background")
async def operations_background(operation_id: str) -> dict:
    """v3.6: flip an IN-FLIGHT run to background mode ("Continue in
    background"). From that moment save_memory refuses unattended writes and
    the renderer watches it for done/parked notifications. Session-scoped —
    a finished run has nothing left to flag and 404s."""
    if not operator_service.mark_background(operation_id):
        raise HTTPException(status_code=404, detail="That operation is not active.")
    return {"ok": True, "id": operation_id, "background": True}


class OperationContinueRequest(BaseModel):
    answer: str = Field(..., min_length=1, description="The operator's answer to a paused operation's question.")


@app.post("/operations/{operation_id}/continue")
async def operations_continue(operation_id: str, payload: OperationContinueRequest) -> StreamingResponse:
    """Resume a paused operation with the operator's answer.

    Streams the continued run as SSE into the same conversation thread. The
    operation reuses its original context/folder/flags, so this is a true
    resume — not a new run.
    """
    return _operation_sse(lambda emit: operator_service.continue_operation(
        operation_id=operation_id, answer=payload.answer, emit=emit,
    ))


# ---------------------------------------------------------------------------
# Grounding sources — paste text / upload a PDF (verified provenance)
# ---------------------------------------------------------------------------


class SourceTextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Source text to ground the next operation.")


@app.post("/sources/stage-text")
async def sources_stage_text(payload: SourceTextRequest) -> dict:
    """Stage a pasted block of text as the grounding source for the next run."""
    try:
        info = operator_service.stage_source(payload.text, "Operator-pasted source")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **info}


# ---------------------------------------------------------------------------
# Operator projects — lightweight grouping for runs (v2.8). Namespaced under
# /operator/projects because the legacy /projects/* routes are the run-folder
# browser (a different "project" concept).
# ---------------------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="Project name.")
    # v3.4: sub-folders. The depth cap (one level, enforced in
    # operation_log_service.create_project) surfaces here as a 400.
    parent_id: str = Field(
        "", description="Parent project id — creates a sub-folder (one level max).")


class ProjectAssignRequest(BaseModel):
    project_id: str = Field(
        "", description="Project or sub-folder id, or empty to unfile the run.")


@app.get("/operator/projects")
async def operator_projects_list() -> dict:
    return {"projects": operation_log_service.list_projects()}


@app.post("/operator/projects")
async def operator_projects_create(payload: ProjectCreateRequest) -> dict:
    try:
        return operation_log_service.create_project(
            payload.name, parent_id=payload.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/operations/{operation_id}/project")
async def operations_assign_project(operation_id: str, payload: ProjectAssignRequest) -> dict:
    if payload.project_id and not operation_log_service.project_exists(payload.project_id):
        raise HTTPException(status_code=404, detail="Unknown project id.")
    updated = operation_log_service.assign_operation_project(operation_id, payload.project_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Unknown operation id.")
    return {"ok": True, "id": operation_id, "project_id": payload.project_id}


@app.post("/sources/clear")
async def sources_clear() -> dict:
    """Discard any staged grounding source."""
    operator_service.clear_staged_source()
    return {"ok": True}


@app.post("/sources/stage-pdf")
async def sources_stage_pdf(file: UploadFile = File(...)) -> dict:
    """Validate + extract an uploaded PDF and stage its text as the grounding
    source for the next run. Refuses image-only/scanned PDFs honestly."""
    data = await file.read()
    try:
        extracted = pdf_service.validate_and_extract(data, file.filename or "upload.pdf")
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    label = f"Attached PDF: {file.filename or 'upload.pdf'}"
    info = operator_service.stage_source(extracted["text"], label)
    return {"ok": True, "pages": extracted["pages"], "truncated": extracted["truncated"], **info}


@app.post("/operations/{operation_id}/upload-source")
async def operations_upload_source(operation_id: str, file: UploadFile = File(...)) -> StreamingResponse:
    """Answer a grounding-gate question with an uploaded PDF: extract its text
    and RESUME the operation grounded in it. Image-only PDFs are refused with a
    400 (no SSE) so the run stays awaiting and honest."""
    data = await file.read()
    try:
        extracted = pdf_service.validate_and_extract(data, file.filename or "upload.pdf")
    except pdf_service.PdfError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)

    operator_service.save_source_pdf(operation_id, data, file.filename or "source.pdf")
    answer = f"[Attached PDF: {file.filename or 'upload.pdf'}]\n\n{extracted['text']}"

    return _operation_sse(lambda emit: operator_service.continue_operation(
        operation_id=operation_id, answer=answer, emit=emit,
    ))


@app.get("/operations/recent")
async def operations_recent(limit: int = 20) -> dict:
    return {"operations": operation_log_service.list_recent(limit=limit)}


class TranscribeRequest(BaseModel):
    """Mic recording from the renderer. Base64 keeps us off multipart parsing."""
    audio_base64: str = Field(..., min_length=1)
    mime: str = "audio/webm"


@app.post("/operations/transcribe")
async def operations_transcribe(payload: TranscribeRequest) -> dict:
    """Voice input → text via OpenAI Whisper. Fills the command box client-side."""
    try:
        text = await asyncio.to_thread(
            transcription_service.transcribe_base64, payload.audio_base64, payload.mime,
        )
    except transcription_service.TranscriptionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"text": text}


# IMPORTANT: register fixed-path routes (/operations/load, /operations/audio,
# /operations/recent) BEFORE the dynamic /operations/{operation_id} route, or
# FastAPI's first-match ordering will swallow them as if "load"/"audio" were
# operation IDs.

@app.get("/operations/load")
async def operations_load(artifact_folder: str) -> dict:
    """Rehydrate a completed Operator run from its on-disk artifacts.

    The renderer uses this to reopen a recent Operator run from the sidebar:
    we return the parsed operation_log.json, the text of sources_packet.md
    and script.md, and a presence flag for audiobook.mp3. Anything expected
    but missing is reported in ``missing`` so the renderer can show a
    warning instead of silently failing.
    """
    try:
        folder = project_service._resolve_project_folder(artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    log_path = folder / "operation_log.json"
    sources_path = folder / "sources_packet.md"
    script_path = folder / "script.md"
    audio_path = folder / "audiobook.mp3"

    operation_log = None
    if log_path.is_file():
        try:
            operation_log = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            operation_log = None

    sources_packet = ""
    if sources_path.is_file():
        try:
            sources_packet = sources_path.read_text(encoding="utf-8")
        except OSError:
            sources_packet = ""

    script_md = ""
    if script_path.is_file():
        try:
            script_md = script_path.read_text(encoding="utf-8")
        except OSError:
            script_md = ""

    has_audio = audio_path.is_file()
    audio_bytes = audio_path.stat().st_size if has_audio else 0

    # Verify the artifacts this operation actually DECLARED — the manifest in
    # its own operation_log — never a global checklist. (The old v1 code
    # expected sources_packet.md / script.md / audiobook.mp3 on every run, an
    # audiobook-era leftover that produced false "missing" warnings for
    # document/deck/sheet operations.) Rules:
    #   - external kinds (Drive/Slides/Sheets/Gmail/browser) are cloud-only:
    #     verified by having an http(s) link, never "missing on disk";
    #   - every other kind is a local file: verified by existence in the run
    #     folder (by declared name, falling back to the recorded path);
    #   - operation_log.json is expected for every operation.
    external_kinds = {"gmail_draft", "drive_folder", "spreadsheet", "slides", "browser"}
    missing: list[str] = []
    if not log_path.is_file():
        missing.append("operation_log.json — expected but not found in the run folder")
    for art in (operation_log.get("artifacts") or []) if operation_log else []:
        name = str(art.get("name") or "").strip()
        kind = str(art.get("kind") or "").strip().lower()
        art_path = str(art.get("path") or "").strip()
        if not name:
            continue
        if kind in external_kinds:
            if not art_path.lower().startswith(("http://", "https://")):
                missing.append(f"{name} — {kind} artifact has no link recorded")
            continue
        local = folder / name
        if not local.is_file() and not (art_path and Path(art_path).is_file()):
            missing.append(f"{name} — declared as a local file but not found in the run folder")

    return {
        "artifact_folder": str(folder),
        "name": folder.name,
        "workflow": "operator",
        "operation_log": operation_log,
        "sources_packet": sources_packet,
        "script": script_md,
        "has_audio": has_audio,
        "audio_bytes": audio_bytes,
        "missing": missing,
    }


@app.get("/operations/audio")
async def operations_audio(artifact_folder: str, filename: str = "audiobook.mp3") -> FileResponse:
    """Serve an audio artifact for inline playback.

    Reuses project_service's path-traversal protection so only files inside
    the configured outputs/ directory can be served.
    """
    if filename != "audiobook.mp3":
        raise HTTPException(status_code=400, detail="Only audiobook.mp3 may be streamed.")
    try:
        folder = project_service._resolve_project_folder(artifact_folder)
    except project_service.ProjectError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    path = folder / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio file not found")
    return FileResponse(str(path), media_type="audio/mpeg", filename=filename)


class MemoryCommitRequest(BaseModel):
    """User decision on a batch of planner-proposed memory updates.

    Each proposal id MUST appear in exactly one of ``confirmed`` or
    ``dismissed``; the operator never auto-decides on the user's behalf.
    """
    confirmed: list[str] = Field(default_factory=list)
    dismissed: list[str] = Field(default_factory=list)


class MemoryCommitResponse(BaseModel):
    operation_id: str
    written: list[dict]                   # what actually got committed to memory
    dismissed: list[str]                  # proposal ids the user chose to drop
    skipped: list[dict]                   # proposals we couldn't write + why


@app.post("/operations/{operation_id}/memory/commit", response_model=MemoryCommitResponse)
async def operations_memory_commit(operation_id: str, payload: MemoryCommitRequest) -> MemoryCommitResponse:
    """Apply a user's decision on a batch of planner-proposed memory updates.

    Approval-only by construction: the planner only ever queues proposals
    via the ``propose_memory_update`` tool. This endpoint is the single
    write path — it reads the proposal from the stored operation log,
    validates it against the existing memory_service write APIs, and
    commits. Dismissed proposals are marked as such on the log; confirmed
    proposals are written through memory_service and also marked.
    """
    rec = operation_log_service.get_operation(operation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="operation not found")

    proposals = {p["id"]: p for p in rec.get("proposed_memory_updates", [])}
    written: list[dict] = []
    skipped: list[dict] = []
    statuses: dict[str, str] = {}

    for prop_id in payload.confirmed:
        prop = proposals.get(prop_id)
        if not prop:
            skipped.append({"id": prop_id, "reason": "proposal not found on this operation"})
            continue
        if prop.get("status") in ("committed", "dismissed"):
            skipped.append({"id": prop_id, "reason": f"already {prop['status']}"})
            continue
        try:
            entry = _commit_proposal(prop, operation_id=operation_id)
            written.append({"id": prop_id, "kind": prop["kind"], "entry_id": entry.get("id", "")})
            statuses[prop_id] = "committed"
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — never let one bad payload tank the batch
            skipped.append({"id": prop_id, "reason": f"{type(exc).__name__}: {exc}"})

    for prop_id in payload.dismissed:
        if prop_id not in proposals:
            skipped.append({"id": prop_id, "reason": "proposal not found on this operation"})
            continue
        if proposals[prop_id].get("status") in ("committed", "dismissed"):
            skipped.append({"id": prop_id, "reason": f"already {proposals[prop_id]['status']}"})
            continue
        statuses[prop_id] = "dismissed"

    if statuses:
        operation_log_service.update_proposal_statuses(operation_id, statuses=statuses)

    return MemoryCommitResponse(
        operation_id=operation_id,
        written=written,
        dismissed=[p for p in payload.dismissed if statuses.get(p) == "dismissed"],
        skipped=skipped,
    )


def _commit_proposal(prop: dict, *, operation_id: str) -> dict:
    """Route a single proposal to the matching memory_service write API.

    Validates payload shape per kind. Anything the planner sent that doesn't
    match the kind's required fields is rejected here, before it touches
    memory state. Every write is provenance-stamped written_by="commit" with
    the operation the proposal came from. Returns the persisted memory entry.
    """
    kind = prop.get("kind")
    payload = prop.get("payload") or {}
    stamp = {"written_by": "commit", "source_op": operation_id}
    if kind == "fact":
        if not str(payload.get("fact", "")).strip():
            raise ValueError("fact proposal missing 'fact' text")
        return memory_service.add_fact({
            "topic": str(payload.get("topic", "") or ""),
            "fact": str(payload.get("fact", "") or ""),
            "source": str(payload.get("source", "") or ""),
        }, **stamp)
    if kind == "contact":
        if not str(payload.get("name", "")).strip():
            raise ValueError("contact proposal missing 'name'")
        return memory_service.add_contact({
            "name": str(payload.get("name", "") or ""),
            "role": str(payload.get("role", "") or ""),
            "company": str(payload.get("company", "") or ""),
            "email": str(payload.get("email", "") or ""),
            "phone": str(payload.get("phone", "") or ""),
            "notes": str(payload.get("notes", "") or ""),
            "last_contact_iso": str(payload.get("last_contact_iso", "") or ""),
        }, **stamp)
    if kind == "follow_up":
        if not str(payload.get("what", "")).strip():
            raise ValueError("follow_up proposal missing 'what'")
        return memory_service.add_follow_up({
            "what": str(payload.get("what", "") or ""),
            "who": str(payload.get("who", "") or ""),
            "due_iso": str(payload.get("due_iso", "") or ""),
            "status": "open",
            "source_run": str(payload.get("source_run", "") or ""),
        }, **stamp)
    if kind == "decision":
        if not str(payload.get("decision", "")).strip():
            raise ValueError("decision proposal missing 'decision'")
        return memory_service.add_decision({
            "decision": str(payload.get("decision", "") or ""),
            "context": str(payload.get("context", "") or ""),
            "date_iso": str(payload.get("date_iso", "") or ""),
        }, **stamp)
    raise ValueError(f"unknown proposal kind: {kind!r}")


@app.get("/operations/{operation_id}")
async def operations_get(operation_id: str) -> dict:
    """Single operation record from the persisted operations.json log.

    Registered AFTER the fixed-path operations routes so that "load"/"audio"
    aren't swallowed as operation IDs.
    """
    rec = operation_log_service.get_operation(operation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="operation not found")
    return rec


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

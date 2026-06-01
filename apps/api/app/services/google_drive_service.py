"""Google Drive integration (approval-only uploads).

Scope: ``drive.file`` only. The app can see and write only files it itself
created — never the user's broader Drive contents.

Security model:
- Credentials JSON lives at ``apps/api/google_credentials.json`` (git-ignored).
- OAuth token lives at ``apps/api/google_token.json`` (git-ignored).
- Neither file's contents is ever returned by an API endpoint or logged.
- Public-facing helpers return only safe metadata (connected state, the
  signed-in email, the Drive folder URL).
- The OAuth flow uses the installed-app pattern (random local port). The
  user's system browser handles the consent screen; no embedded webview.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .artifact_service import outputs_dir
from .settings_service import load_settings

log = logging.getLogger("ridian.google")

# Narrowest scopes per capability. Never request full Drive or Gmail.
#
#   drive.file      — see + create only the files this app itself makes.
#   gmail.compose   — create + edit Gmail drafts. CAN NOT send. Drafts sit
#                     in the user's Drafts folder until they decide to send,
#                     matching the memo's approval philosophy: drafts are
#                     not external action, sends are.
#
# Adding gmail.compose requires every previously-connected user to reconnect
# Google Drive once so the new scope is consented. The renderer surfaces
# this as "insufficient scope — Reconnect Google" rather than failing silently.
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.compose",
]

# apps/api/app/services/google_drive_service.py -> apps/api/<file>
_API_DIR = Path(__file__).resolve().parent.parent.parent
CREDENTIALS_PATH = _API_DIR / "google_credentials.json"
TOKEN_PATH = _API_DIR / "google_token.json"

# Files allowed to ride to Drive. Matches the open-file allowlist in
# export_service, minus task.txt's exclusion (we DO want task.txt on Drive).
UPLOAD_ALLOWED_FILENAMES: tuple[str, ...] = (
    "task.txt",
    # Business workflow
    "research_summary.md",
    "business_document.md",
    "slide_outline.md",
    "draft_email.md",
    "business_document.docx",
    "slide_outline.pptx",
    # Social Media Production workflow
    "social_content_package.md",
    "script.md",
    "caption_package.md",
    "posting_checklist.md",
    "visual_production.md",
    # Agentic Advances Daily Brief
    "agentic_advances_brief.md",
    # NotebookLM Package
    "notebooklm_package.md",
    # Operator v1 — finished business artifacts
    "sources_packet.md",
    "script.md",
    "audiobook.mp3",
    "operation_log.json",
    # Uploaded thumbnail/image input
    "input_thumbnail.png",
    "input_thumbnail.jpg",
    "input_thumbnail.jpeg",
    "input_thumbnail.webp",
)

_MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".json": "application/json",
}

FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveError(Exception):
    """Raised by helpers when a call is invalid or fails.

    ``status`` is the HTTP status the API layer should return. ``detail`` is
    safe to surface to the operator — never contains tokens or secrets.
    """

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def credentials_present() -> bool:
    return CREDENTIALS_PATH.exists()


def _load_credentials() -> Optional[Credentials]:
    """Load + refresh the saved token. Returns None when nothing usable exists."""
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception as exc:  # noqa: BLE001 — many parse errors possible
        log.warning("google.token_load_failed type=%s", type(exc).__name__)
        return None

    if not creds:
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as exc:  # noqa: BLE001
            log.warning("google.token_refresh_failed type=%s", type(exc).__name__)
            return None
    return None


def _build_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Public status
# ---------------------------------------------------------------------------


def get_status() -> dict:
    """Safe public status. Never returns token contents."""
    creds = _load_credentials()
    if not creds or not creds.valid:
        return {"connected": False, "email": None}
    try:
        service = _build_service(creds)
        about = service.about().get(fields="user(emailAddress)").execute()
        email = (about or {}).get("user", {}).get("emailAddress")
        return {"connected": True, "email": email}
    except Exception as exc:  # noqa: BLE001
        log.warning("google.status_lookup_failed type=%s", type(exc).__name__)
        return {"connected": False, "email": None}


# ---------------------------------------------------------------------------
# OAuth flow (installed app)
# ---------------------------------------------------------------------------


def run_oauth_flow() -> dict:
    """Run the installed-app OAuth flow.

    Blocks the calling thread until the user finishes in their browser.
    Endpoints should call this via ``asyncio.to_thread`` so uvicorn stays
    responsive to other requests (such as the /google/status poll).
    """
    if not credentials_present():
        raise GoogleDriveError(
            "google_credentials.json is missing. Create an OAuth Client ID "
            "(Desktop app) in Google Cloud Console, download the JSON, and "
            "save it to apps/api/google_credentials.json. See QUICKSTART.md "
            "for the full walkthrough.",
            status=400,
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    except Exception as exc:  # noqa: BLE001
        log.warning("google.flow_init_failed type=%s", type(exc).__name__)
        raise GoogleDriveError(
            "google_credentials.json could not be parsed. Make sure it is a "
            "Desktop-app OAuth client JSON downloaded from Google Cloud Console.",
            status=400,
        ) from exc

    try:
        # port=0 picks a random free port. open_browser=True launches the
        # default system browser (Electron's renderer is NEVER involved).
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("google.oauth_flow_failed type=%s", type(exc).__name__)
        raise GoogleDriveError(
            f"OAuth flow did not complete ({type(exc).__name__}). Close the "
            "browser tab and try again.",
            status=500,
        ) from exc

    try:
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    except OSError as exc:
        log.warning("google.token_write_failed type=%s", type(exc).__name__)
        raise GoogleDriveError(
            "OAuth succeeded but the token could not be saved to disk.",
            status=500,
        ) from exc

    log.info("google.connected")
    return get_status()


def disconnect() -> dict:
    """Remove the saved token. Always returns disconnected status."""
    if TOKEN_PATH.exists():
        try:
            TOKEN_PATH.unlink()
            log.info("google.disconnected")
        except OSError as exc:
            log.warning("google.token_unlink_failed type=%s", type(exc).__name__)
    return {"connected": False, "email": None}


# ---------------------------------------------------------------------------
# Artifact upload
# ---------------------------------------------------------------------------


def _resolve_artifact_folder(folder_str: str) -> Path:
    """Same validation as export_service: must live inside outputs/."""
    if not folder_str:
        raise GoogleDriveError("artifact_folder is required.", status=400)
    try:
        candidate = Path(folder_str).resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise GoogleDriveError(
            f"Invalid folder path ({type(exc).__name__}).", status=400
        ) from exc

    outputs = outputs_dir().resolve()
    try:
        candidate.relative_to(outputs)
    except ValueError as exc:
        raise GoogleDriveError(
            "Folder is not inside the configured outputs directory.", status=400
        ) from exc

    if not candidate.exists():
        raise GoogleDriveError("Artifact folder does not exist.", status=404)
    if not candidate.is_dir():
        raise GoogleDriveError("Artifact path is not a directory.", status=400)
    if candidate.resolve() == outputs:
        raise GoogleDriveError(
            "Refusing to operate on the outputs root. Provide a per-run folder.",
            status=400,
        )

    return candidate


def _mime_for(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Drive folder hierarchy (organize uploads under a stable tree)
# ---------------------------------------------------------------------------

# Files we use to detect the workflow type by presence inside the artifact folder.
_SOCIAL_MARKERS = (
    "social_content_package.md",
    "script.md",
    "caption_package.md",
    "posting_checklist.md",
)
_BUSINESS_MARKERS = (
    "research_summary.md",
    "business_document.md",
    "slide_outline.md",
    "draft_email.md",
)
_AGENTIC_MARKERS = ("agentic_advances_brief.md",)
_NOTEBOOKLM_MARKERS = ("notebooklm_package.md",)
_OPERATOR_MARKERS = ("operation_log.json",)
_AUDIOBOOK_MARKERS = ("audiobook.mp3",)

# Root path every upload lands under. Concrete subfolders are appended per workflow.
_DRIVE_ROOT_PATH = ["Ridian Technologies", "Ridian Agency"]

# When the operator configures an existing Drive folder as the root, we treat
# that folder as the "Ridian Technologies" parent and skip recreating it.
_CONFIGURED_ROOT_SUBPATH = ["Ridian Agency"]


def _read_channel_from_task(folder: Path) -> str:
    """Pull the ``Channel:`` line out of task.txt if present. Empty otherwise."""
    task_file = folder / "task.txt"
    if not task_file.exists():
        return ""
    try:
        for line in task_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*Channel\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    except OSError as exc:
        log.warning("google.task_read_failed type=%s", type(exc).__name__)
    return ""


def map_channel_to_path(channel: str) -> list[str]:
    """Return the path-parts (under Ridian Agency) for a social media channel.

    Unknown / blank channels are filed under Social Media / Custom so they
    still land in the social hierarchy rather than at the root.
    """
    c = (channel or "").strip().lower()
    if "open gulf" in c and "tiktok" in c:
        return ["Social Media", "Open Gulf", "TikTok"]
    if "open gulf" in c and "youtube" in c:
        return ["Social Media", "Open Gulf", "YouTube"]
    if "open gulf" in c and "instagram" in c:
        return ["Social Media", "Open Gulf", "Instagram"]
    if "open gulf" in c and "linkedin" in c:
        return ["Social Media", "Open Gulf", "LinkedIn"]
    # "twitter" is unique enough to identify the X / Twitter channel without
    # false positives against other Open Gulf platforms.
    if "open gulf" in c and "twitter" in c:
        return ["Social Media", "Open Gulf", "X Twitter"]
    if "buns" in c and "tiktok" in c:
        return ["Social Media", "Buns1562", "TikTok"]
    if "ridian technologies" in c and "linkedin" in c:
        return ["Social Media", "Ridian Technologies", "LinkedIn"]
    if "custom" in c or not c:
        return ["Social Media", "Custom"]
    # Fallback for any other named channel we haven't mapped.
    return ["Social Media", "Custom"]


def infer_drive_destination(artifact_folder: Path) -> list[str]:
    """Decide the full Drive folder path (under My Drive, excluding the run
    folder itself). Always starts with the Ridian Technologies / Ridian Agency
    root so even unknown workflow types stay categorized.
    """
    has_social = any((artifact_folder / f).is_file() for f in _SOCIAL_MARKERS)
    has_business = any((artifact_folder / f).is_file() for f in _BUSINESS_MARKERS)
    has_agentic = any((artifact_folder / f).is_file() for f in _AGENTIC_MARKERS)
    has_notebooklm = any((artifact_folder / f).is_file() for f in _NOTEBOOKLM_MARKERS)
    has_audiobook = any((artifact_folder / f).is_file() for f in _AUDIOBOOK_MARKERS)
    has_operator = any((artifact_folder / f).is_file() for f in _OPERATOR_MARKERS)

    # Operator runs win over plain "business" detection — they carry their own
    # operation log and are categorized by deliverable (audiobook vs. other).
    if has_audiobook or (has_operator and "audiobook" in artifact_folder.name.lower()):
        return list(_DRIVE_ROOT_PATH) + ["Audio Briefings"]
    if has_operator:
        return list(_DRIVE_ROOT_PATH) + ["Operations"]
    if has_social:
        channel = _read_channel_from_task(artifact_folder)
        return list(_DRIVE_ROOT_PATH) + map_channel_to_path(channel)
    if has_agentic:
        return list(_DRIVE_ROOT_PATH) + ["Agentic Briefs"]
    if has_notebooklm:
        return list(_DRIVE_ROOT_PATH) + ["NotebookLM"]
    if has_business:
        return list(_DRIVE_ROOT_PATH) + ["Business Workflows"]
    # Unknown: file under Business Workflows as a sensible default.
    log.info("google.infer_destination.unknown folder=%s -> Business Workflows", artifact_folder.name)
    return list(_DRIVE_ROOT_PATH) + ["Business Workflows"]


def find_or_create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Return the Drive folder id for ``name`` under ``parent_id`` (or root).

    Idempotent: if a folder of that name already exists *that the app can
    see*, reuse it; otherwise create one. With the narrow ``drive.file``
    scope the app can only see folders it itself created — see the
    "Limitations" note in the module docstring.
    """
    # Escape backslashes and single quotes for the Drive query language.
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    q_parts = [
        f"name = '{safe_name}'",
        f"mimeType = '{FOLDER_MIME}'",
        "trashed = false",
    ]
    q_parts.append(f"'{parent_id}' in parents" if parent_id else "'root' in parents")
    q = " and ".join(q_parts)

    try:
        res = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name)",
                pageSize=10,
            )
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.warning("google.folder_lookup_failed name=%s status=%s", name, status)
        raise GoogleDriveError(
            f"Drive folder lookup failed (HTTP {status}).", status=502
        ) from exc

    files = res.get("files", [])
    if files:
        return files[0]["id"]

    body = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    try:
        created = service.files().create(body=body, fields="id").execute()
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.warning("google.folder_create_failed name=%s status=%s", name, status)
        raise GoogleDriveError(
            f"Drive folder create failed (HTTP {status}).", status=502
        ) from exc
    return created["id"]


def ensure_drive_path(
    service,
    path_parts: list[str],
    *,
    root_parent_id: Optional[str] = None,
) -> tuple[Optional[str], list[str]]:
    """Walk ``path_parts`` top-down, creating missing folders.

    Returns ``(final_parent_id, names_actually_used)``. If ``root_parent_id``
    is supplied the walk starts inside that folder; otherwise it starts at My
    Drive root.
    """
    parent_id: Optional[str] = root_parent_id
    names: list[str] = []
    for name in path_parts:
        parent_id = find_or_create_folder(service, name, parent_id)
        names.append(name)
    return parent_id, names


def _normalize_root_folder_id(raw: Optional[str]) -> str:
    """Return a bare folder ID from the operator's settings value.

    Accepts either a raw ID or a pasted Drive folder URL. Returns "" when the
    input is blank.
    """
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    m = re.search(r"/folders/([A-Za-z0-9_\-]+)", s)
    if m:
        return m.group(1)
    return s


def _get_configured_root_id() -> str:
    return _normalize_root_folder_id(
        load_settings().get("google_drive_root_folder_id")
    )


def _lookup_root_folder_name(service, folder_id: str) -> Optional[str]:
    """Best-effort name lookup for the configured root folder.

    With the narrow ``drive.file`` scope the app generally cannot read folders
    it did not create, so this often fails silently. Returns ``None`` on any
    failure so the caller can fall back to a generic display label.
    """
    try:
        meta = (
            service.files()
            .get(fileId=folder_id, fields="id, name, mimeType, trashed")
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.info("google.root_lookup_unavailable status=%s", status)
        return None
    if meta.get("mimeType") != FOLDER_MIME or meta.get("trashed"):
        return None
    return meta.get("name")


def upload_artifact_folder(folder_str: str) -> dict:
    """Create a Drive folder + upload allowlisted files. Never auto-fires.

    Uploads into a stable Drive hierarchy:

        My Drive / Ridian Technologies / Ridian Agency / <category>

    where ``<category>`` is "Business Workflows", or for social media runs
    "Social Media / <brand> / <platform>" inferred from the local artifact
    files and the ``Channel:`` line in ``task.txt``.
    """
    folder = _resolve_artifact_folder(folder_str)
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GoogleDriveError(
            "Google Drive is not connected. Open Settings to connect first.",
            status=400,
        )

    service = _build_service(creds)

    # Resolve (and create if needed) the parent hierarchy. Idempotent —
    # repeated uploads to the same category reuse the same folders rather
    # than spawning duplicates.
    drive_path_parts = infer_drive_destination(folder)

    configured_root_id = _get_configured_root_id()
    if configured_root_id:
        log.info(
            "google.upload root_mode=configured_root id=%s",
            configured_root_id,
        )
        # Treat the configured folder as the "Ridian Technologies" root and
        # walk from "Ridian Agency" downward inside it.
        if drive_path_parts and drive_path_parts[0] == "Ridian Technologies":
            path_under_root = drive_path_parts[1:]
        else:
            path_under_root = list(drive_path_parts)
        root_display = (
            _lookup_root_folder_name(service, configured_root_id)
            or "Ridian Technologies"
        )
        log.info(
            "google.upload configured_root display_name=%s walk=%s",
            root_display,
            " / ".join(path_under_root),
        )
        try:
            parent_id, child_names = ensure_drive_path(
                service, path_under_root, root_parent_id=configured_root_id
            )
        except GoogleDriveError as exc:
            raise GoogleDriveError(
                "The configured Google Drive root folder could not be "
                "accessed. Check the folder ID in Settings (Google Workspace "
                "→ Google Drive root folder ID) or reconnect Google Drive.",
                status=400,
            ) from exc
        parent_names = [root_display] + child_names
    else:
        log.info("google.upload root_mode=default_root")
        parent_id, parent_names = ensure_drive_path(service, drive_path_parts)

    # Create the per-run folder INSIDE the resolved parent.
    try:
        drive_folder = (
            service.files()
            .create(
                body={
                    "name": folder.name,
                    "mimeType": FOLDER_MIME,
                    "parents": [parent_id] if parent_id else [],
                },
                fields="id, name, webViewLink",
            )
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.warning("google.folder_create_failed status=%s", status)
        if configured_root_id and status in (400, 403, 404):
            raise GoogleDriveError(
                "The configured Google Drive root folder could not be "
                "accessed. Check the folder ID in Settings (Google Workspace "
                "→ Google Drive root folder ID) or reconnect Google Drive.",
                status=400,
            ) from exc
        raise GoogleDriveError(
            f"Drive folder create failed (HTTP {status}).", status=502
        ) from exc

    folder_id = drive_folder["id"]
    folder_url = drive_folder.get("webViewLink", "")
    folder_name = drive_folder["name"]

    # Build the upload candidate list: allowlisted files in the run folder
    # plus the sibling ZIP at outputs/<basename>.zip if it exists.
    candidates: list[Path] = []
    for name in UPLOAD_ALLOWED_FILENAMES:
        p = folder / name
        if p.is_file():
            candidates.append(p)

    sibling_zip = folder.parent / f"{folder.name}.zip"
    if sibling_zip.is_file():
        # Defense in depth: verify the sibling zip is also inside outputs/
        try:
            sibling_zip.resolve().relative_to(outputs_dir().resolve())
            candidates.append(sibling_zip)
        except ValueError:
            log.warning("google.sibling_zip_outside_outputs path=%s", sibling_zip)

    uploaded: list[str] = []
    upload_errors: list[str] = []
    for path in candidates:
        try:
            media = MediaFileUpload(str(path), mimetype=_mime_for(path), resumable=False)
            service.files().create(
                body={"name": path.name, "parents": [folder_id]},
                media_body=media,
                fields="id, name",
            ).execute()
            uploaded.append(path.name)
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", "?")
            log.warning("google.file_upload_failed name=%s status=%s", path.name, status)
            upload_errors.append(f"{path.name} (HTTP {status})")
        except OSError as exc:
            log.warning("google.file_read_failed name=%s type=%s", path.name, type(exc).__name__)
            upload_errors.append(f"{path.name} (read failed)")

    if not uploaded:
        raise GoogleDriveError(
            "Drive folder created but no files uploaded successfully. "
            + ("Errors: " + "; ".join(upload_errors) if upload_errors else ""),
            status=502,
        )

    drive_path_str = " / ".join(parent_names + [folder_name])

    log.info(
        "google.upload_complete drive_path=%s uploaded=%d failed=%d",
        drive_path_str,
        len(uploaded),
        len(upload_errors),
    )

    return {
        "status": "success",
        "drive_folder_name": folder_name,
        "drive_folder_url": folder_url,
        "drive_path": drive_path_str,
        "uploaded_files": uploaded,
    }

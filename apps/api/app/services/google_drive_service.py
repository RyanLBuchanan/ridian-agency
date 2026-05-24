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
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .artifact_service import outputs_dir

log = logging.getLogger("ridian.google")

# Narrowest scope: only files this app creates. Never request full drive.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# apps/api/app/services/google_drive_service.py -> apps/api/<file>
_API_DIR = Path(__file__).resolve().parent.parent.parent
CREDENTIALS_PATH = _API_DIR / "google_credentials.json"
TOKEN_PATH = _API_DIR / "google_token.json"

# Files allowed to ride to Drive. Matches the open-file allowlist in
# export_service, minus task.txt's exclusion (we DO want task.txt on Drive).
UPLOAD_ALLOWED_FILENAMES: tuple[str, ...] = (
    "task.txt",
    "research_summary.md",
    "business_document.md",
    "slide_outline.md",
    "draft_email.md",
    "business_document.docx",
    "slide_outline.pptx",
)

_MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
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


def upload_artifact_folder(folder_str: str) -> dict:
    """Create a Drive folder + upload allowlisted files. Never auto-fires."""
    folder = _resolve_artifact_folder(folder_str)
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GoogleDriveError(
            "Google Drive is not connected. Open Settings to connect first.",
            status=400,
        )

    service = _build_service(creds)

    try:
        drive_folder = (
            service.files()
            .create(
                body={"name": folder.name, "mimeType": FOLDER_MIME},
                fields="id, name, webViewLink",
            )
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.warning("google.folder_create_failed status=%s", status)
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

    log.info(
        "google.upload_complete drive_folder=%s uploaded=%d failed=%d",
        folder_name,
        len(uploaded),
        len(upload_errors),
    )

    return {
        "status": "success",
        "drive_folder_name": folder_name,
        "uploaded_files": uploaded,
        "drive_folder_url": folder_url,
    }

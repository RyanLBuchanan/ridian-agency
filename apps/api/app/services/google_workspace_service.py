"""Google Sheets + Slides deliverables (v1.6).

Real business deliverables, not Markdown: ``create_spreadsheet`` writes a
live Google Sheet (headers, rows, formulas via USER_ENTERED, bold + frozen
header, autosized columns); ``create_presentation`` builds a live Google
Slides deck (title slide + TITLE_AND_BODY slides with real bullets).

Scope note: both the Sheets and Slides APIs accept the narrow ``drive.file``
scope for files the app itself creates — which is exactly what we do here.
No new OAuth consent is needed; the user just has to enable the two APIs in
the same Google Cloud project that owns google_credentials.json:

    https://console.cloud.google.com/apis/library/sheets.googleapis.com
    https://console.cloud.google.com/apis/library/slides.googleapis.com

If an API isn't enabled, Google returns 403 accessNotConfigured — we catch
that and surface the enable-link so the operator can fix it in two clicks.

After creation we make a best-effort move of the file into the app's
"Ridian Operator / Spreadsheets" (or "/ Decks") Drive folder so deliverables
stay organized; if the move fails the file is still usable in My Drive root.
"""

from __future__ import annotations

import logging
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .google_drive_service import (
    _load_credentials,
    _build_service as _build_drive_service,
    find_or_create_folder,
)

log = logging.getLogger("ridian.workspace")

_SHEETS_ENABLE_URL = "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
_SLIDES_ENABLE_URL = "https://console.cloud.google.com/apis/library/slides.googleapis.com"


class GoogleWorkspaceError(Exception):
    """Renderer-safe failure. ``detail`` never contains tokens or payloads."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _require_creds():
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise GoogleWorkspaceError(
            "Google is not connected. Open Settings → Connect Google Drive first.",
            status=400,
        )
    return creds


def _api_not_enabled(exc: HttpError) -> bool:
    try:
        return exc.resp.status == 403 and b"accessNotConfigured" in (exc.content or b"")
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Pure request builders (unit-testable without network)
# ---------------------------------------------------------------------------


def sheet_format_requests(column_count: int) -> list[dict]:
    """Bold + freeze the header row, autosize all columns."""
    return [
        {"repeatCell": {
            "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"autoResizeDimensions": {
            "dimensions": {"sheetId": 0, "dimension": "COLUMNS",
                           "startIndex": 0, "endIndex": max(1, column_count)},
        }},
    ]


def slide_requests(
    default_slide_id: Optional[str],
    slide_titles: list[str],
    slide_bullets: list[list[str]],
) -> list[dict]:
    """Build the batchUpdate request list for a whole deck.

    Slide 0 with no bullets becomes a TITLE layout (big centered title);
    everything else is TITLE_AND_BODY with real disc bullets.
    """
    requests: list[dict] = []
    if default_slide_id:
        # presentations.create makes one default slide we don't want.
        requests.append({"deleteObject": {"objectId": default_slide_id}})

    for i, title in enumerate(slide_titles):
        bullets = slide_bullets[i] if i < len(slide_bullets) else []
        slide_id = f"ridian_slide_{i}"
        title_id = f"ridian_title_{i}"
        body_id = f"ridian_body_{i}"

        if i == 0 and not bullets:
            requests.append({"createSlide": {
                "objectId": slide_id,
                "insertionIndex": i,
                "slideLayoutReference": {"predefinedLayout": "TITLE"},
                "placeholderIdMappings": [
                    {"layoutPlaceholder": {"type": "CENTERED_TITLE"}, "objectId": title_id},
                ],
            }})
            requests.append({"insertText": {"objectId": title_id, "text": title}})
            continue

        requests.append({"createSlide": {
            "objectId": slide_id,
            "insertionIndex": i,
            "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
            "placeholderIdMappings": [
                {"layoutPlaceholder": {"type": "TITLE"}, "objectId": title_id},
                {"layoutPlaceholder": {"type": "BODY"}, "objectId": body_id},
            ],
        }})
        requests.append({"insertText": {"objectId": title_id, "text": title}})
        body_text = "\n".join(b.strip() for b in bullets if b.strip())
        if body_text:
            requests.append({"insertText": {"objectId": body_id, "text": body_text}})
            requests.append({"createParagraphBullets": {
                "objectId": body_id,
                "textRange": {"type": "ALL"},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }})
    return requests


# ---------------------------------------------------------------------------
# Drive filing (best-effort, never fails the deliverable)
# ---------------------------------------------------------------------------


def _file_into_operator_folder(file_id: str, subfolder: str) -> None:
    """Move a created file into Ridian Operator / <subfolder>. Best-effort."""
    try:
        creds = _load_credentials()
        drive = _build_drive_service(creds)
        parent = None
        for name in ("Ridian Operator", subfolder):
            parent = find_or_create_folder(drive, name, parent)
        meta = drive.files().get(fileId=file_id, fields="parents").execute()
        prev = ",".join(meta.get("parents", []))
        drive.files().update(
            fileId=file_id, addParents=parent, removeParents=prev, fields="id"
        ).execute()
        log.info("workspace.filed id=%s folder=Ridian Operator/%s", file_id, subfolder)
    except Exception as exc:  # noqa: BLE001 — filing is cosmetic
        log.info("workspace.file_move_skipped type=%s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def create_spreadsheet(title: str, headers: list[str], rows: list[list[str]]) -> dict:
    """Create a live Google Sheet. Returns {"spreadsheet_id", "url"}.

    ``rows`` values go in with USER_ENTERED, so "=D2-C2" style formulas and
    numbers are parsed natively by Sheets.
    """
    if not title or not title.strip():
        raise GoogleWorkspaceError("Spreadsheet title is required.", status=400)
    if not headers:
        raise GoogleWorkspaceError("At least one column header is required.", status=400)

    creds = _require_creds()
    try:
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        created = (
            sheets.spreadsheets()
            .create(body={"properties": {"title": title.strip()}},
                    fields="spreadsheetId,spreadsheetUrl")
            .execute()
        )
        sid = created["spreadsheetId"]
        values = [list(headers)] + [list(r) for r in rows]
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range="A1",
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": sheet_format_requests(len(headers))},
        ).execute()
    except HttpError as exc:
        if _api_not_enabled(exc):
            raise GoogleWorkspaceError(
                "The Google Sheets API isn't enabled for your Google Cloud "
                f"project yet. Enable it here (two clicks): {_SHEETS_ENABLE_URL} "
                "— then run the command again.",
                status=400,
            ) from exc
        status = getattr(getattr(exc, "resp", None), "status", "?")
        raise GoogleWorkspaceError(
            f"Google Sheets call failed (HTTP {status}).", status=502
        ) from exc

    _file_into_operator_folder(sid, "Spreadsheets")
    url = created.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{sid}"
    log.info("workspace.sheet_created id=%s cols=%d rows=%d", sid, len(headers), len(rows))
    return {"spreadsheet_id": sid, "url": url}


def create_presentation(
    title: str, slide_titles: list[str], slide_bullets: list[list[str]]
) -> dict:
    """Create a live Google Slides deck. Returns {"presentation_id", "url"}."""
    if not title or not title.strip():
        raise GoogleWorkspaceError("Deck title is required.", status=400)
    if not slide_titles:
        raise GoogleWorkspaceError("At least one slide is required.", status=400)

    creds = _require_creds()
    try:
        slides = build("slides", "v1", credentials=creds, cache_discovery=False)
        pres = slides.presentations().create(body={"title": title.strip()}).execute()
        pid = pres["presentationId"]
        default_id = None
        existing = pres.get("slides") or []
        if existing:
            default_id = existing[0].get("objectId")
        reqs = slide_requests(default_id, slide_titles, slide_bullets)
        if reqs:
            slides.presentations().batchUpdate(
                presentationId=pid, body={"requests": reqs}
            ).execute()
    except HttpError as exc:
        if _api_not_enabled(exc):
            raise GoogleWorkspaceError(
                "The Google Slides API isn't enabled for your Google Cloud "
                f"project yet. Enable it here (two clicks): {_SLIDES_ENABLE_URL} "
                "— then run the command again.",
                status=400,
            ) from exc
        status = getattr(getattr(exc, "resp", None), "status", "?")
        raise GoogleWorkspaceError(
            f"Google Slides call failed (HTTP {status}).", status=502
        ) from exc

    _file_into_operator_folder(pid, "Decks")
    url = f"https://docs.google.com/presentation/d/{pid}"
    log.info("workspace.deck_created id=%s slides=%d", pid, len(slide_titles))
    return {"presentation_id": pid, "url": url}

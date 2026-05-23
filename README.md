# Ridian Agency

A local desktop app that turns a business task into a polished package:
market research summary, business document, slide outline, and a draft
email — all in one ~90-second run, all saved to a folder on your machine.

Built on Python (FastAPI + the official OpenAI Agents SDK) for the backend
and Electron for the desktop GUI. Local-first, no cloud, no auth, no
database.

## New here? Read [QUICKSTART.md](QUICKSTART.md)

A non-developer-friendly, step-by-step Windows setup guide. ~15 minutes
to clone, install, configure your OpenAI key in the desktop Settings
panel, and run your first workflow.

## What you get

- **Five OpenAI Agents** wired in a sequential pipeline: research → writer
  → reviewer → presentation → email. Whole run is wrapped in a single
  Agents SDK `trace`.
- **Desktop GUI** (Electron) with a Settings panel, a prompt library, live
  backend status, copy buttons on every result, and an approval-only
  "send draft email" action.
- **Local artifacts** written to `outputs/<timestamp>_<slug>/` — five
  Markdown files per run plus the original task.
- **One-click launcher**: `Start-Ridian-Agency.bat` starts the backend and
  the desktop app.

## Local-only by design

- The API server binds to `127.0.0.1:8000` (loopback only). Nothing
  outside your machine can reach it.
- The OpenAI key and SMTP password live on disk in
  `apps/api/local_settings.json` (saved via the Settings panel) or
  `apps/api/.env`. Both files are git-ignored.
- The desktop renderer talks to the backend over plain HTTP. CSP locks
  network access to `http://127.0.0.1:8000` only.
- Secrets are never logged, never returned by any API endpoint, and
  never shown to the renderer after they're saved. The Settings panel
  shows `*_configured: true` flags instead.

## Settings live in the desktop GUI

You don't need to edit environment variables for normal use. Launch the
app, click **Settings** in the top-right header, fill in:

- **AI provider** — OpenAI API key (required), model (defaults to
  `gpt-4o-mini`).
- **Operator profile** — your name, email, company name.
- **Default email recipient** — where the Approve & Send button delivers.
- **SMTP credentials** — only needed for the email send button.

Settings persist to `apps/api/local_settings.json` and take precedence
over any values in `apps/api/.env`. If neither is set, the GUI shows a
first-run banner pointing you at Settings; the **Run workflow** button
stays disabled until an OpenAI key is configured.

## Developer setup (Windows PowerShell)

End-users should follow [QUICKSTART.md](QUICKSTART.md). The condensed
version for developers:

```powershell
# clone
git clone <repo-url>
cd ridian-agency

# Python backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r apps\api\requirements.txt

# Desktop GUI
cd desktop
npm install
cd ..

# Launch (backend + desktop together)
.\Start-Ridian-Agency.bat
```

If you'd rather run pieces by hand:

```powershell
# Backend only
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000 --app-dir apps\api

# Desktop only (in a second terminal)
cd desktop
npm run start
```

Useful URLs (backend only):

| URL | Purpose |
| --- | --- |
| <http://127.0.0.1:8000>            | The same operator console served as static HTML |
| <http://127.0.0.1:8000/docs>       | Swagger UI for the API |
| <http://127.0.0.1:8000/health>     | `{ openai_key_loaded, model, ... }` |
| <http://127.0.0.1:8000/settings>   | GET / POST settings (never returns secrets) |
| <http://127.0.0.1:8000/workflows/run> | POST the workflow |
| <http://127.0.0.1:8000/email/send-approved> | POST to send an approved email |

## Architecture

```
desktop/                       Electron shell (renderer + preload + main)
  renderer/{index.html, styles.css, app.js}
  main.js, preload.js, start.js
  package.json
apps/api/
  app/
    main.py                    FastAPI app, all routes
    agents/                    5 specialist agents + triage (agents-as-tools)
    services/
      workflow_service.py      Sequential pipeline, wrapped in one SDK trace
      artifact_service.py      Writes outputs/<timestamp>_<slug>/*.md
      email_delivery_service.py  SMTP via stdlib smtplib + ssl
      settings_service.py      JSON-backed settings + env mirror
    prompts/                   .txt prompt files, one per agent
  requirements.txt
  .env.example
outputs/                       One folder per workflow run (git-ignored)
Start-Ridian-Agency.bat        Double-click launcher (Windows)
Start-Ridian-Agency.ps1        The real launcher logic
QUICKSTART.md                  Setup for non-developers
README.md
```

Pipeline:

```
operator task
   |
   v
research_agent     -> research_summary.md
writer_agent       -> draft document
reviewer_agent     -> business_document.md
presentation_agent -> slide_outline.md
email_agent        -> draft_email.md
```

A single `trace("ridian-agency.workflow")` wraps the whole pipeline so it
shows up as one workflow at <https://platform.openai.com/traces>.

## Files that must never be committed

These are already in [.gitignore](.gitignore). Double-check before pushing:

- `apps/api/.env` — contains your OpenAI key and SMTP password if you used
  the env-var route.
- `apps/api/local_settings.json` — contains your OpenAI key and SMTP
  password as saved through the desktop Settings panel.
- `outputs/` (except `outputs/.gitkeep`) — generated artifacts may contain
  task content you don't want to publish.
- `.venv/` — your local Python environment.
- `desktop/node_modules/` — Electron + npm install tree (hundreds of MB).

If you're about to push, a quick paranoia check:

```powershell
git status
git diff --cached --name-only | Select-String -Pattern "(\.env|local_settings\.json|outputs/|\.venv/|node_modules/)"
```

The Select-String should return nothing.

## Future Google Workspace exports (planned, not built)

The current export layer is local-only:

- **Local exports (built):** Open artifact folder in Explorer, open
  individual artifact files in their default app, export the whole run
  as a ZIP, export `business_document.md` as a real `.docx`, export
  `slide_outline.md` as a real `.pptx`. All from `POST /artifacts/*`
  endpoints that validate paths against the configured outputs
  directory.

A natural next step is uploading the same artifacts to the operator's
Google Workspace account. The planned flow:

1. **Connect Google account (OAuth, one time).** A "Connect Google"
   button in Settings opens a Google OAuth consent screen in the
   system browser. The OAuth tokens land in
   `apps/api/local_settings.json` (or a sibling file) and never reach
   the renderer.
2. **Approval-only uploads.** Same model as the email send button:
   nothing uploads until the operator clicks an explicit approval
   button in the GUI. No background sync, no auto-upload.
3. **Per-artifact destinations:**
   - **Upload folder to Drive** — `POST /artifacts/upload-drive` zips
     the run and uploads to a configured Drive folder (or creates one
     named after the run).
   - **Create a Google Doc from `business_document.md`** —
     `POST /artifacts/export-google-doc` converts the Markdown to a
     real Google Doc.
   - **Create a Google Slides deck from `slide_outline.md`** —
     `POST /artifacts/export-google-slides` converts the slide outline
     into a real deck with speaker notes.
4. **Scopes:** narrow `drive.file` (only files the app created) plus
   `documents` and `presentations`. Never `drive` (full Drive access).
5. **Revocation:** a "Disconnect Google" button in Settings revokes
   the token locally and removes it from `local_settings.json`.

Until that ships, use the local exports above — you can drag the
generated `.docx`, `.pptx`, or ZIP into Google Drive yourself.

See `apps/api/app/services/export_service.py` for the local export
implementation, and look for `TODO(google-workspace)` markers when
that work begins.

## Roadmap (intentionally not built yet)

- Google Workspace upload (see the section above)
- Database / persistent run history
- User auth, Microsoft OAuth
- Real web-search tool wired into the research agent
- Streaming responses
- Bundling Python with the desktop app for true single-installer distribution

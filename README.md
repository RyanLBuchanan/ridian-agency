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

## Two workflow modes

The desktop app has a **Workflow mode** selector at the top:

- **Business Workflow** — the original five-agent pipeline (research →
  writer → reviewer → presentation → email) that produces a research
  summary, business document, slide outline, and a draft email.
- **Social Media Production** — a single-agent guided workflow that
  turns a brief (channel, starting point, format, notes, goal, depth)
  into a four-section package: content concept, script, caption package,
  and a posting checklist. Built for Open Gulf (TikTok + YouTube), Buns
  (TikTok), and custom channels.

Both modes save artifacts under `outputs/<timestamp>_<slug>/`. The
artifact actions (Open folder, Copy path, Export ZIP, Upload to Google
Drive) work for either mode's runs.

### Social Media Production — supported channels & outputs

| Channel | Outputs in the Content Package section |
| --- | --- |
| Open Gulf TikTok | Angle · 3 hooks · 30-60s script summary · shot list · text overlays · visual style · CTA · repurpose |
| Open Gulf YouTube | 3 titles · thumbnail concepts · long-form outline · intro hook · segment talking points · B-roll · description · chapters · Shorts cut-downs · CTA |
| Buns TikTok | Concept · 3 hooks · voiceover · shot list · captions · edit notes · optional recurring series |
| Custom | Sensible defaults — angle, hooks, format-appropriate body, edit notes, CTA |

Three depth options:

- **Quick post package** — ~250-400 words per section.
- **Full production package** — detailed shot lists, multiple thumbnail
  concepts, fuller scripts.
- **Weekly content plan** — a 7-day plan with daily topic, platform,
  hook, format, filming notes, caption direction, CTA, and repurpose
  opportunities.

The "Starting point" dropdown lets the operator say *I have existing
footage* / *transcript* / *finished script*. In those cases the agent
**does not invent new topics** — it interprets the supplied material
and proposes the best angle, hook options, edit sequence, and overlays
for what's already there.

### Posting is manual (by design)

Ridian Agency **does not post anywhere automatically.** It produces
review-ready content packages and saves them locally. You read every
section, edit as needed, then publish yourself in TikTok / YouTube /
Instagram / wherever. Each social card shows a *Review concept* /
*Review script* / *Review caption* / *Review posting checklist* marker
as a visual reminder.

Direct platform-API posting (TikTok, YouTube, Instagram) is on the
roadmap but intentionally not built — see the section at the end of
this README.

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

## Google Workspace integration

### Built today: Drive upload

After any workflow you can click **Upload to Google Drive** in the
desktop Actions card. The app creates a new folder in your Drive
(named after the local artifact folder) and uploads:

- `task.txt`, `research_summary.md`, `business_document.md`,
  `slide_outline.md`, `draft_email.md`
- `business_document.docx` and `slide_outline.pptx` if you've exported them
- The sibling `<basename>.zip` if you've exported it

The OAuth client lives at `apps/api/google_credentials.json` (yours, from
Google Cloud Console). The token lives at `apps/api/google_token.json`
(written after consent). **Both files are git-ignored.** The OAuth scope
is the narrow `https://www.googleapis.com/auth/drive.file` — the app can
only see files it itself created in your Drive, never the rest.

Endpoints (all local, loopback only):

- `GET /google/status` — `{ connected, email }`. Never returns tokens.
- `POST /google/connect` — starts the installed-app OAuth flow (opens
  your system browser via `InstalledAppFlow.run_local_server`). Blocks
  the calling request until consent finishes; uvicorn stays responsive
  to other endpoints because the flow runs in `asyncio.to_thread`.
- `POST /google/disconnect` — deletes `google_token.json`.
- `POST /google/upload-artifacts` — validates the folder is inside
  `outputs/`, creates a Drive folder, uploads allowlisted files,
  returns the folder URL.

See [QUICKSTART.md](QUICKSTART.md#google-drive-setup-optional) for the
full setup (Google Cloud Console → OAuth client → consent → connect).

### Planned: Google Docs / Slides conversion

Native Drive uploads keep the `.docx` and `.pptx` as Office files. A
natural next step is converting them to live Google Docs and Slides:

- **Google Doc from `business_document.md`** — `POST /google/export-doc`
  using Drive's `application/vnd.google-apps.document` import type.
- **Google Slides from `slide_outline.md`** — `POST /google/export-slides`
  using `application/vnd.google-apps.presentation`.

Both stay approval-only, both stay on the `drive.file` scope. Look for
`TODO(google-workspace)` in
[apps/api/app/services/export_service.py](apps/api/app/services/export_service.py)
when that work begins.

## Roadmap (intentionally not built yet)

- Direct platform-API posting (TikTok, YouTube, Instagram). For now,
  Ridian Agency produces review-ready content packages and you publish
  yourself.
- Google Docs / Slides conversion via Drive's import mime types (the
  Drive folder upload is built; direct conversion to live Docs/Slides
  is next).
- Multi-step approval cascade for Social Media Production (today the
  full four-section package is generated in one run; v2 would gate each
  section behind explicit operator approval).
- Database / persistent run history.
- User auth, Microsoft OAuth.
- Real web-search tool wired into the research agent.
- Streaming responses.
- Bundling Python with the desktop app for true single-installer distribution.

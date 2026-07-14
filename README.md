# Ridian Agency

A local desktop app that turns a business task into a polished package:
market research summary, business document, slide outline, and a draft
email — all in one ~90-second run, all saved to a folder on your machine.

Built on Python (FastAPI + the official Anthropic SDK, with Claude powering
every agent) for the backend
and Electron for the desktop GUI. Local-first, no cloud, no auth, no
database.

## New here? Read [QUICKSTART.md](QUICKSTART.md)

A non-developer-friendly, step-by-step Windows setup guide. ~15 minutes
to clone, install, configure your Anthropic key in the desktop Settings
panel, and run your first workflow.

## What you get

- **Five Claude agents** wired in a sequential pipeline: research → writer
  → reviewer → presentation → email — plus the Ridian Operator, a
  tool-calling planner built on the Anthropic SDK's tool runner.
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
- The Anthropic key and SMTP password live on disk in
  `apps/api/local_settings.json` (saved via the Settings panel) or
  `apps/api/.env`. Both files are git-ignored.
- The desktop renderer talks to the backend over plain HTTP. CSP locks
  network access to `http://127.0.0.1:8000` only.
- Secrets are never logged, never returned by any API endpoint, and
  never shown to the renderer after they're saved. The Settings panel
  shows `*_configured: true` flags instead.

## Desktop layout

The window is a real desktop console with a persistent left sidebar and
a main workspace:

- **Sidebar (left)** — brand block, **+ New workflow**, the two workflow
  modes (Business / Social Media Production), an **Outputs** list that
  appears once a run is selected, a **Recent runs** list (loaded from
  the local `outputs/` folder so old runs survive restarts), and
  **Settings** at the bottom.
- **Workspace (right)** — header with the current run's title plus
  Backend / Drive status pills. The body switches between **Welcome**
  (first launch), the active workflow's **input form**, the **running**
  spinner, and the **run results**.

After a workflow completes, the input form collapses into a compact
**Current run summary** at the top of the workspace, with **Edit task /
Run again / New workflow** controls. Output panels become tabs in the
sidebar — clicking a tab swaps the one visible panel; nothing scrolls.

Recent runs persist across app restarts via the backend's
`/projects/recent` and `/projects/load` endpoints (allowlisted files
only, path-validated against `outputs/`).

## Desktop shortcut + taskbar pinning

To launch Ridian Agency by clicking a real desktop icon (and to pin it
to the Windows taskbar):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Create-Ridian-Agency-Shortcut.ps1
```

(Or right-click `scripts\Create-Ridian-Agency-Shortcut.ps1` → **Run with
PowerShell**.) A **Ridian Agency** shortcut appears on your Desktop with
the bundled icon.

To pin it to the taskbar (do this in the right order or Windows pins
the running process instead of the shortcut):

1. **If a Ridian Agency icon is already pinned**, right-click it →
   **Unpin from taskbar** first. Otherwise you'll end up with two.
2. **Recommended:** drag the **Ridian Agency** shortcut from the Desktop
   directly onto your taskbar. Windows pins the `.lnk`, so clicking the
   pinned icon always re-invokes `Start-Ridian-Agency.bat`.
3. **Alternative:** double-click the Desktop shortcut to launch, then
   right-click the running Ridian Agency icon in the taskbar →
   **Pin to taskbar**. This relies on Windows matching the running
   process's AppUserModelID (`com.ridiantechnologies.ridianagency`,
   set by `desktop/main.js`) to the same AUMID on the `.lnk`.

If you see "generic Electron" launching from your pinned icon, it means
Windows pinned an Electron instance from before the AUMID was set, or
pinned the running process when the `.lnk` itself wasn't AUMID-tagged.
Unpin and re-pin using step 2 above.

From then on, clicking the pinned icon launches both the backend and the
desktop window.

The icon files live at `desktop/assets/icon.png` (BrowserWindow) and
`desktop/assets/icon.ico` (Windows shortcut). Replace either with your
own brand asset whenever you're ready — Electron and the shortcut script
will pick up the new icon automatically. Re-generate the placeholder
with:

```powershell
.\.venv\Scripts\python.exe desktop\assets\generate_icon.py
```

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
| Open Gulf LinkedIn | Post angle · 3 hooks · visual suggestion · repurpose idea. Text-first; the **main 900-1,500-char LinkedIn post** lives in the Caption Package section (along with an optional shorter version, suggested first comment, 3-6 hashtags, CTA). Script section says "Not applicable" unless format is video. |
| Buns TikTok | Concept · 3 hooks · voiceover · shot list · captions · edit notes · optional recurring series |
| Ridian Technologies LinkedIn | Same structure as Open Gulf LinkedIn, plus a one-line **soft business-development angle** identifying who would naturally reach out after reading. Voice is professional/practical/calm, never salesy. |
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

- **AI provider — Anthropic** — Anthropic API key (required; get one at
  <https://console.anthropic.com/settings/keys>), model (defaults to
  `claude-opus-4-8`).
- **Voice input (OpenAI Whisper)** — optional OpenAI API key, used ONLY
  for microphone transcription. Everything else runs on Claude.
- **Operator profile** — your name, email, company name.
- **Default email recipient** — where the Approve & Send button delivers.
- **SMTP credentials** — only needed for the email send button.

Settings persist to `apps/api/local_settings.json` and take precedence
over any values in `apps/api/.env`. If neither is set, the GUI shows a
first-run banner pointing you at Settings; the **Run workflow** button
stays disabled until an Anthropic key is configured.

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
| <http://127.0.0.1:8000/health>     | `{ anthropic_key_loaded, model, ... }` |
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

The pipeline runs as five sequential Claude calls; each step's output is
written to disk before the next begins.

## Files that must never be committed

These are already in [.gitignore](.gitignore). Double-check before pushing:

- `apps/api/.env` — contains your API keys and SMTP password if you used
  the env-var route.
- `apps/api/local_settings.json` — contains your API keys and SMTP
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
desktop Actions card. The app creates a per-run folder inside a stable
hierarchy in your Drive and uploads:

- `task.txt`, `research_summary.md`, `business_document.md`,
  `slide_outline.md`, `draft_email.md`
- `business_document.docx` and `slide_outline.pptx` if you've exported them
- The sibling `<basename>.zip` if you've exported it

The OAuth client lives at `apps/api/google_credentials.json` (yours, from
Google Cloud Console). The token lives at `apps/api/google_token.json`
(written after consent). **Both files are git-ignored.** The OAuth scope
is the narrow `https://www.googleapis.com/auth/drive.file` — the app can
only see files it itself created in your Drive, never the rest.

### Drive folder organization

Uploads land inside a stable hierarchy so your Drive stays tidy as runs
accumulate:

```text
My Drive/
  Ridian Technologies/
    Ridian Agency/
      Business Workflows/
        <timestamp>_<slug>/         <-- one folder per business run
      Social Media/
        Open Gulf/
          TikTok/
            <timestamp>_<slug>/
          YouTube/
            <timestamp>_<slug>/
          LinkedIn/
            <timestamp>_<slug>/
        Buns1562/
          TikTok/
            <timestamp>_<slug>/
        Ridian Technologies/
          LinkedIn/
            <timestamp>_<slug>/
        Custom/
          <timestamp>_<slug>/        <-- channels we don't recognize
```

How the destination is chosen for each upload:

1. **Workflow type** is inferred from the files inside the local artifact
   folder. Presence of `social_content_package.md` (or the other social
   markers) → Social Media. Presence of `business_document.md` (or the
   other business markers) → Business Workflows.
2. For social runs, **channel** is read from the `Channel:` line in
   `task.txt` (written by `social_media_workflow_service.py`). The
   channel string is matched case-insensitively against the four known
   patterns; anything else falls under `Social Media / Custom`.
3. Parent folders are **idempotent** — the second upload of an Open Gulf
   TikTok run reuses the same `Ridian Technologies / Ridian Agency /
   Social Media / Open Gulf / TikTok` chain rather than creating
   duplicates. (Limitation: with the narrow `drive.file` scope the app
   can only see folders it itself created. If you create
   "Ridian Technologies" by hand in your Drive, the app cannot see it
   and will create its own — unless you point it at the existing folder
   via the optional setting described below.)
4. **Existing uploads at the Drive root from earlier versions are left
   alone.** Only new uploads use the new hierarchy.

### Reusing an existing Drive folder as the root (optional)

If you already have a "Ridian Technologies" folder in My Drive and don't
want Ridian Agency to create a second one, point the app at the existing
folder via **Settings → Google Workspace → Google Drive root folder ID**.

How to get the folder ID:

1. Open the desired folder in Google Drive.
2. Copy the ID from the URL after `/folders/`. For
   `drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456`
   the ID is `1AbCdEfGhIjKlMnOpQrStUvWxYz123456`.
3. Paste it into **Settings → Google Workspace → Google Drive root
   folder ID**. The Settings panel also accepts a pasted full URL — the
   folder ID is extracted automatically.
4. Click **Save settings**.

When configured, uploads land at:

```text
<your existing folder>/
  Ridian Agency/
    Business Workflows/
    Social Media/...
```

Leave the field blank to restore the default behavior (the app creates
and reuses its own top-level `Ridian Technologies` folder).

If the folder ID is wrong or the configured folder is inaccessible, the
upload fails with a clear message ("The configured Google Drive root
folder could not be accessed…") instead of silently creating duplicates.
The value lives in `apps/api/local_settings.json` next to other local
preferences and is git-ignored.

The upload success message in the desktop window shows the full path so
you know exactly where the run landed, for example:

```text
Uploaded 7 files to Google Drive: Ridian Technologies / Ridian Agency /
Social Media / Open Gulf / TikTok / 20260524-132542_open-gulf-tiktok-…
```

The **Open Drive folder** link still opens the per-run folder directly.

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

### Future integrations

- **Google Calendar** — create approved content calendar events after
  a Social Media Production run.
- **Microsoft 365** — Outlook Calendar (event creation), Outlook Mail
  (draft email send, parallel to the SMTP path), OneDrive (artifact
  export, parallel to the Google Drive path).
- Both would reuse the same approval-only model the SMTP and Drive
  paths already use — nothing auto-sends or auto-syncs.

### Other planned work

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

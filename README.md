# Ridian Agency — Local MVP

## Start with one click

Once setup is done (Python venv created, `apps/api/.env` filled in, and
`desktop/npm install` run — see the sections below), starting the whole stack
is a double-click.

1. **Double-click `Start-Ridian-Agency.bat`** in the repo root.
2. A PowerShell window opens, checks dependencies, starts the backend (or
   reuses the one already on port 8000), and launches the desktop app.
3. When the desktop window appears and the pill reads **Backend online**,
   you're ready.

**If Windows blocks the script:** right-click the `.bat` → **Run as
administrator**, or open PowerShell once and run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

(The `.bat` invokes PowerShell with `-ExecutionPolicy Bypass` so this is
usually unnecessary, but some lockdown policies block even that.)

**To stop everything:** close the **Ridian Agency** desktop window, then
close the **Ridian Agency — Backend (uvicorn)** PowerShell window. The
launcher window self-closes after about 8 seconds; no action needed.

A local prototype of a multi-agent "agency" built on the official
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

Submit a business task and get back:

1. A market research summary
2. A polished business document
3. A slide deck outline
4. A draft outbound email
5. All four saved to disk as `.md` artifacts

No frontend. No database. No auth. Just a FastAPI service and a folder.

## How it works

```
operator task
   │
   ▼
[research_agent]  ──► research_summary.md
   │
   ▼
[writer_agent]    ──► draft document
   │
   ▼
[reviewer_agent]  ──► business_document.md
   │
   ▼
[presentation_agent] ──► slide_outline.md
   │
   ▼
[email_agent]     ──► draft_email.md
```

The pipeline runs inside a single Agents SDK `trace("ridian-agency.workflow")`
so the whole run shows up as one workflow in the OpenAI tracing dashboard.

`apps/api/app/agents/triage_agent.py` also exposes the five specialists as
**agents-as-tools** for ad-hoc requests where you don't want the full pipeline.

## Project layout

```
ridian-agency/
  apps/
    api/
      app/
        main.py                  # FastAPI app
        agents/
          triage_agent.py        # orchestrator (agents-as-tools)
          research_agent.py      # + function_tool example (get_today)
          writer_agent.py
          reviewer_agent.py
          presentation_agent.py
          email_agent.py
        services/
          artifact_service.py    # writes files under outputs/
          workflow_service.py    # sequential pipeline + tracing
        prompts/
          research_prompt.txt
          writer_prompt.txt
          reviewer_prompt.txt
          presentation_prompt.txt
          email_prompt.txt
      requirements.txt
      .env.example
  outputs/                       # one timestamped folder per run
  README.md
```

## Setup (Windows PowerShell)

From the repo root:

```powershell
cd c:\Users\ryanl\Desktop\Ryan\Ridian_Technologies\ridian-agency

# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# If PowerShell blocks the activation script, run this once for the current user:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 2. Install dependencies
pip install --upgrade pip
pip install -r apps\api\requirements.txt

# 3. Configure environment
Copy-Item apps\api\.env.example apps\api\.env
notepad apps\api\.env   # paste your OPENAI_API_KEY
```

## Run the API

```powershell
# From the repo root, with the venv activated:
$env:PYTHONPATH = "apps\api"
uvicorn app.main:app --reload --port 8000 --app-dir apps\api
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

## Try it

### Health check

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### Run the workflow

```powershell
$body = @{
  task = "Research practical AI consulting opportunities for small businesses in Gulf Shores, Orange Beach, Foley, and Fairhope Alabama."
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/workflows/run `
  -ContentType "application/json" `
  -Body $body
```

The response will look like:

```json
{
  "status": "complete",
  "artifact_folder": "C:\\...\\ridian-agency\\outputs\\20260521-143022_research-practical-ai-consulting",
  "research_summary": "...",
  "business_document": "...",
  "slide_outline": "...",
  "draft_email": "..."
}
```

And under `outputs/<timestamp>_<slug>/` you will find:

- `research_summary.md`
- `business_document.md`
- `slide_outline.md`
- `draft_email.md`
- `task.txt`

### Open the interactive docs

```powershell
Start-Process http://127.0.0.1:8000/docs
```

## Configuration

| Variable         | Default        | Purpose                                  |
| ---------------- | -------------- | ---------------------------------------- |
| `OPENAI_API_KEY` | _(required)_   | OpenAI key used by the Agents SDK.       |
| `OPENAI_MODEL`   | `gpt-4o-mini`  | Model used by every agent.               |
| `OUTPUTS_DIR`    | `./outputs`    | Where artifact folders are written.      |

## Tracing

The Agents SDK uses OpenAI's hosted tracing by default. With `OPENAI_API_KEY`
set, every `/workflows/run` call shows up as a `ridian-agency.workflow` trace
at <https://platform.openai.com/traces>.

## Send the draft email (SMTP, approval-only)

Every workflow run produces a draft email — but Ridian Agency **never auto-sends**
it. To deliver the draft to your inbox, click **Approve & Send Email to Me** on
the Draft Email card in the desktop app. The renderer asks for a confirmation,
then `POST`s to `/email/send-approved`. The backend reads SMTP credentials from
environment variables and sends the message.

### Recommended SMTP setup

Add these keys to `apps/api/.env` (they're already templated in
`apps/api/.env.example`). The endpoint returns a clear, graceful 503 if any are
missing — the workflow itself still works.

| Variable           | Example                | Notes                                                |
| ------------------ | ---------------------- | ---------------------------------------------------- |
| `SMTP_HOST`        | `smtp.gmail.com`       | Gmail / Office 365 / Fastmail / your own server.    |
| `SMTP_PORT`        | `587`                  | `587` for STARTTLS, `465` for implicit TLS.         |
| `SMTP_USERNAME`    | `you@gmail.com`        | Usually your full email address.                    |
| `SMTP_PASSWORD`    | _(app password)_       | For Gmail / Workspace, **App Password**, not your account password. |
| `SMTP_FROM_EMAIL`  | `you@gmail.com`        | The `From:` address (most providers require it match `SMTP_USERNAME`). |
| `DEFAULT_TO_EMAIL` | `you@yourdomain.com`   | Where to send when the request omits `to_email`.   |

**Gmail App Password:** turn on 2-Step Verification, then create one at
<https://myaccount.google.com/apppasswords>. Paste it into `SMTP_PASSWORD` with
no spaces.

### Privacy & safety

- Credentials live in `apps/api/.env` only. The desktop renderer never sees them.
- The endpoint never returns or logs `SMTP_PASSWORD`, and never echoes raw SMTP
  server text (which on some servers can leak state).
- The Approve & Send button never auto-fires — it requires a click and a
  confirmation each time.

## Desktop GUI (Electron)

A native-feeling desktop window lives in [desktop/](desktop/). It's a thin
Electron shell that loads a local HTML/CSS/JS renderer, talks to the FastAPI
backend on `http://127.0.0.1:8000`, and shows a live backend-status pill.

It does **not** bundle Python — the backend runs separately. Start the API
first (per the steps above), then launch the desktop app.

### Setup & run (Windows PowerShell)

```powershell
cd c:\Users\ryanl\Desktop\Ryan\Ridian_Technologies\ridian-agency\desktop
npm install
npm run start
```

A window titled **Ridian Agency** opens. The pill in the top-right reads
*Backend online* (green) when `/health` responds, *Backend offline* (red)
otherwise. If it's offline, a banner explains how to start the API.

### Layout

```
desktop/
  package.json
  main.js          # Electron main process, BrowserWindow, CSP, secure defaults
  preload.js       # exposes only window.ridian.backendOrigin to the renderer
  renderer/
    index.html
    styles.css
    app.js
```

Renderer security: `contextIsolation: true`, `nodeIntegration: false`,
`sandbox: true`, plus a CSP that restricts network calls to
`http://127.0.0.1:8000`. The API key never reaches the renderer; it stays in
`apps/api/.env` and is read by the Python backend.

## Roadmap (intentionally not built yet)

- Database / persistent run history
- Auth
- Real web search tool wired into the research agent
- Streaming responses
- Bundling Python with the desktop app and auto-starting the backend

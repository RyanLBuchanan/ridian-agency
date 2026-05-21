# Ridian Agency — Local MVP

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

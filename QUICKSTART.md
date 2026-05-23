# Ridian Agency — Quickstart (Windows)

A step-by-step guide for setting up Ridian Agency on a new Windows machine.
Written for someone who is comfortable copy-pasting commands but isn't
necessarily a developer. ~15 minutes start to finish.

## What is Ridian Agency?

A local desktop app that turns a business task into a polished package:
a market research summary, a business document, a slide outline, and a
draft email. Everything runs on your computer; nothing is uploaded except
the prompts you send to OpenAI to power the agents.

## What you'll install

| Tool       | Why                                                  |
| ---------- | ---------------------------------------------------- |
| Git        | To clone the repo from GitHub.                       |
| Python 3.11+ | The backend (FastAPI + OpenAI Agents SDK) runs in Python. |
| Node.js 18+ | The desktop app (Electron) is a Node program.       |
| OpenAI API key | Required for the agents to call the model.       |

You'll also need a free OpenAI account to create the API key at
<https://platform.openai.com/api-keys>.

## 1. Prerequisites — install once per machine

If you already have any of these, skip that step.

**Git:** <https://git-scm.com/download/win> — pick the default options in the installer.

**Python:** <https://www.python.org/downloads/windows/> — during install, check **"Add python.exe to PATH"**.

**Node.js (LTS):** <https://nodejs.org/> — the default installer adds it to PATH.

After installing, open a fresh PowerShell window and verify:

```powershell
git --version
python --version
node --version
npm --version
```

All four should print a version number.

## 2. Clone the repo

Pick a folder you can find again (Desktop is fine). In PowerShell:

```powershell
cd $HOME\Desktop
git clone <your-repo-url> ridian-agency
cd ridian-agency
```

Replace `<your-repo-url>` with the GitHub URL of this project.

## 3. Install backend dependencies

Still from the `ridian-agency` folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r apps\api\requirements.txt
```

If PowerShell blocks `Activate.ps1`, run this once for your user account
(say "Yes" when prompted), then re-run the activate command:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 4. Install desktop dependencies

```powershell
cd desktop
npm install
cd ..
```

`npm install` takes a minute or two — it downloads Electron.

## 5. Launch the app

Double-click **`Start-Ridian-Agency.bat`** in the repo root.

You should see:
- A launcher window that prints status and self-closes in a few seconds.
- A **Ridian Agency -- Backend (uvicorn)** PowerShell window (leave it open).
- The **Ridian Agency** desktop window.

If you see *"OpenAI API key is not configured"* in the desktop window —
that's expected on first run. Continue to step 6.

## 6. First-run setup — configure your settings

In the Ridian Agency desktop window, click **Settings** in the top right.

Fill in:

**AI provider**
- **OpenAI API key** — paste your key from <https://platform.openai.com/api-keys>.
- **Model** — leave as `gpt-4o-mini` unless you want a different OpenAI model.

**Operator profile** (optional, used as personal defaults later)
- Operator name
- Operator email
- Company name

**Default email recipient** (optional — only needed if you'll use the email feature)
- Default recipient — e.g. your own email so the Approve & Send button sends to you.

**SMTP credentials** (optional — leave blank to skip the email feature for now)
- See the *SMTP setup notes* section below if you want to use Gmail.

Click **Save settings**. You should see a green "Settings saved" message.

The orange "OpenAI API key is not configured" banner should disappear and
the **Run workflow** button becomes active.

## 7. Run your first workflow

In the desktop window:

1. Click any prompt under **Suggested productivity prompts**, or type your own task in the textarea.
2. Click **Run workflow**.
3. Wait 60-90 seconds while the five agents run.

When it's done you'll see four result cards: Research summary, Business
document, Slide outline, Draft email. Each has a **Copy** button. The
Draft Email card also has **Approve & Send Email to Me** (only works if
you configured SMTP in step 6).

## 8. Find the outputs

Every run saves a folder under `outputs/` at the repo root, named with
the date/time plus a slug of the task. Inside:

- `research_summary.md`
- `business_document.md`
- `slide_outline.md`
- `draft_email.md`
- `task.txt` (the original task)

The exact path is also shown in the **Artifact folder** card after each
run, and in Settings under "Output location".

## 9. Stopping the app

1. Close the **Ridian Agency** desktop window.
2. Close the **Ridian Agency -- Backend (uvicorn)** PowerShell window.

That's it.

## SMTP setup notes (optional)

The "Approve & Send Email to Me" button uses SMTP. The most common setup
is Gmail with an **App Password**:

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>
2. Create an App Password: <https://myaccount.google.com/apppasswords>
   (you must have 2-Step Verification on for this page to appear)
3. In Ridian Agency → Settings, fill in:
   - SMTP host: `smtp.gmail.com`
   - SMTP port: `587`
   - SMTP username: your full Gmail address
   - SMTP password: the App Password from step 2 (16 characters, no spaces)
   - From email: same as SMTP username
4. Click **Save settings**, then **Test email settings** at the bottom
   of the modal. You should get a test email within a minute.

Other providers work the same way; common settings:

| Provider           | Host                | Port |
| ------------------ | ------------------- | ---- |
| Gmail / Workspace  | smtp.gmail.com      | 587  |
| Outlook / Office 365 | smtp.office365.com | 587  |
| Fastmail           | smtp.fastmail.com   | 465  |

## Troubleshooting

**"Backend is not running"** in the desktop window
The backend PowerShell window may have closed. Re-launch the app with
`Start-Ridian-Agency.bat`. If the backend window shows a Python error,
read the last few lines — most often it's a missing package (re-run
`pip install -r apps\api\requirements.txt` inside the venv).

**"Python environment not found"** when launching
You haven't run step 3 yet. Open PowerShell in the repo root and run:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r apps\api\requirements.txt
```

**"Desktop dependencies not installed"** when launching
You haven't run step 4 yet. Open PowerShell in the repo root and run:
```powershell
cd desktop
npm install
cd ..
```

**PowerShell blocks `Activate.ps1`**
Run this once and accept the prompt, then try again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**OpenAI API key is missing / "OpenAI API key is not configured" banner**
Click **Settings** in the desktop window's top right, paste your key in
the **AI provider** section, and click **Save settings**. The banner
disappears immediately.

**"SMTP not configured" when clicking Approve & Send**
You haven't filled in SMTP credentials in Settings. See the *SMTP setup
notes* above. The workflow itself doesn't need SMTP — only the email
send button does.

**Port 8000 is already in use**
Something else is listening on the port the backend needs. Find it:
```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```
Then either stop that process, or close it via Task Manager (look up
the `OwningProcess` ID). Re-launch Ridian Agency.

**Workflow fails with an OpenAI error**
- Check your API key in Settings is correct.
- Check your OpenAI account has credit at <https://platform.openai.com/usage>.
- Check the model name in Settings is a real OpenAI model (default
  `gpt-4o-mini` always works).

## Safety notes

- **Do not enter sensitive, private, or regulated data** in tasks
  (patient records, legal client data, financial PII, etc.). Task content
  is sent to OpenAI to power the agents.
- **Outputs are saved locally** under `outputs/`. They are not uploaded
  anywhere. If a task contains sensitive content, the artifact files on
  disk will too.
- **Email sends only after you click Approve.** Workflows never
  auto-email — you confirm each send.
- **API keys and SMTP passwords stay on your computer.** They live in
  `apps/api/local_settings.json` (or `apps/api/.env` if you used that
  instead). Both files are excluded from Git and never uploaded.
- **Treat `local_settings.json` like any other secret file.** Don't share
  it, don't commit it, don't paste it into chat. If you suspect it leaked,
  rotate your OpenAI key and SMTP App Password immediately.

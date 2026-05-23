# Ridian Agency — launcher (real logic).
#
# Started by Start-Ridian-Agency.bat. Safe to run directly too.
# Idempotent on the backend: if /health already responds it just reuses
# the running server.

$ErrorActionPreference = 'Stop'

$RepoRoot    = $PSScriptRoot
$VenvPython  = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$EnvFile     = Join-Path $RepoRoot 'apps\api\.env'
$ApiDir      = Join-Path $RepoRoot 'apps\api'
$DesktopDir  = Join-Path $RepoRoot 'desktop'
$NodeModules = Join-Path $DesktopDir 'node_modules'

$HealthUrl   = 'http://127.0.0.1:8000/health'

$Host.UI.RawUI.WindowTitle = 'Ridian Agency — Launcher'

function Write-Step { param([string]$Msg) Write-Host ">  $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "OK $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "!  $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "X  $Msg" -ForegroundColor Red }

function Test-Backend {
  try {
    $r = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

Write-Host ''
Write-Host '  Ridian Agency' -ForegroundColor White
Write-Host "  $RepoRoot" -ForegroundColor DarkGray
Write-Host ''

# ---------- 1. dependency checks ----------

Write-Step 'Checking Python virtual environment...'
if (-not (Test-Path $VenvPython)) {
  Write-Err 'Python environment not found.'
  Write-Host '   See QUICKSTART.md step 2 (Install). In short:'
  Write-Host '     python -m venv .venv'
  Write-Host '     .\.venv\Scripts\Activate.ps1'
  Write-Host '     pip install -r apps\api\requirements.txt'
  exit 1
}
Write-Ok 'venv present'

Write-Step 'Checking Electron deps (desktop\node_modules)...'
if (-not (Test-Path $NodeModules)) {
  Write-Err 'Desktop dependencies not installed.'
  Write-Host '   See QUICKSTART.md step 3 (Install). In short:'
  Write-Host '     cd desktop'
  Write-Host '     npm install'
  Write-Host '     cd ..'
  exit 1
}
Write-Ok 'desktop deps present'

# .env and local_settings.json are both optional at launcher time. The
# desktop GUI will show a first-run banner pointing to Settings if no
# OpenAI key is configured.
if (Test-Path $EnvFile) {
  Write-Ok '.env present (settings panel can still override)'
} else {
  Write-Warn 'No apps\api\.env found.'
  Write-Host '   That is OK -- you can configure your OpenAI key from the desktop Settings panel.'
}

$SettingsFile = Join-Path $ApiDir 'local_settings.json'
if (Test-Path $SettingsFile) {
  Write-Ok 'local_settings.json present'
} else {
  Write-Warn 'No local_settings.json yet.'
  Write-Host '   The Settings panel will create it after your first save.'
}

# ---------- 2. backend ----------

Write-Step "Checking backend at $HealthUrl..."
if (Test-Backend) {
  Write-Ok 'backend already running -- reusing'
} else {
  Write-Host '   backend not running, starting in a new window'

  # Each spawned PowerShell sets its own window title, switches to the right
  # directory, then runs the long-lived command. -NoExit keeps the window
  # open so the user can see logs and close it to stop the process.
  $backendCommand = @"
`$Host.UI.RawUI.WindowTitle = 'Ridian Agency -- Backend (uvicorn)'
Set-Location '$RepoRoot'
Write-Host 'Starting FastAPI backend...' -ForegroundColor Cyan
& '$VenvPython' -m uvicorn app.main:app --port 8000 --app-dir '$ApiDir'
"@

  Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $backendCommand) `
    -WindowStyle Normal | Out-Null

  Write-Step 'Waiting for backend to come up (up to 60s)...'
  $deadline = (Get-Date).AddSeconds(60)
  $up = $false
  while ((Get-Date) -lt $deadline) {
    if (Test-Backend) { $up = $true; break }
    Start-Sleep -Milliseconds 750
  }

  if (-not $up) {
    Write-Err 'backend did not respond within 60s'
    Write-Host '   Look at the backend PowerShell window for the error.'
    exit 1
  }
  Write-Ok 'backend online'
}

# ---------- 3. desktop app ----------

Write-Step 'Launching desktop app (Electron)...'

$desktopCommand = @"
`$Host.UI.RawUI.WindowTitle = 'Ridian Agency -- Desktop (Electron)'
Set-Location '$DesktopDir'
Write-Host 'Starting Electron...' -ForegroundColor Cyan
npm run start
"@

Start-Process -FilePath 'powershell.exe' `
  -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $desktopCommand) `
  -WindowStyle Normal | Out-Null

Write-Host ''
Write-Ok 'Ridian Agency is ready'
Write-Host ''
Write-Host '  Backend  : http://127.0.0.1:8000'        -ForegroundColor DarkGray
Write-Host '  Web UI   : http://127.0.0.1:8000'        -ForegroundColor DarkGray
Write-Host '  API docs : http://127.0.0.1:8000/docs'   -ForegroundColor DarkGray
Write-Host ''
Write-Host '  To stop: close the desktop app, then close the backend PowerShell window.' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  This launcher window will close in 8 seconds. Press any key to close now.' -ForegroundColor DarkGray

$timeout = 8
while ($timeout -gt 0) {
  if ([Console]::KeyAvailable) {
    [Console]::ReadKey($true) | Out-Null
    break
  }
  Start-Sleep -Seconds 1
  $timeout--
}

exit 0

<#
Run the frozen backend in a fully sandboxed harness context (v4.4 state-guard).

HARNESS POLICY — the three rules this script embodies:
  1. Sandbox env vars (RIDIAN_SANDBOX / RIDIAN_DATA_DIR / RIDIAN_PORT) are
     passed to the CHILD PROCESS ONLY, via ProcessStartInfo. Never `setx`,
     never `$env:` in a shell you keep using, never [Environment]::Set* —
     leaked vars block the real app's launch (its preflight refuses them).
  2. Scratch data dir + scratch port, never the real store or port 8000
     (the backend's own gate refuses both, but don't lean on the airbag).
  3. Stop the child by the PID printed below — NEVER `taskkill /IM
     ridian-backend.exe`, which would also kill the real app's backend.

Usage:  powershell -File scripts\Run-Sandboxed-Backend.ps1 [-Port 8765]
#>
param(
  [int]$Port = 8765,
  [string]$DataDir = (Join-Path $env:TEMP ("ridian-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))),
  [string]$Exe = (Join-Path $PSScriptRoot '..\desktop\dist\win-unpacked\resources\backend\ridian-backend.exe')
)

if ($Port -eq 8000) { throw 'Sandbox must not use the real port 8000 — pick a scratch port.' }
if (-not (Test-Path $Exe)) { throw "Frozen backend not found at $Exe — run 'npm run build' in desktop/ first." }
New-Item -ItemType Directory -Force $DataDir | Out-Null

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Resolve-Path $Exe).Path
$psi.WorkingDirectory = Split-Path (Resolve-Path $Exe).Path
$psi.UseShellExecute = $false
$psi.EnvironmentVariables['RIDIAN_SANDBOX'] = '1'
$psi.EnvironmentVariables['RIDIAN_DATA_DIR'] = $DataDir
$psi.EnvironmentVariables['RIDIAN_PORT'] = "$Port"

$p = [System.Diagnostics.Process]::Start($psi)
Write-Output ("sandboxed backend  pid={0}  http://127.0.0.1:{1}  data={2}" -f $p.Id, $Port, $DataDir)
Write-Output ("stop it with:      Stop-Process -Id {0}" -f $p.Id)
Write-Output ("clean up with:     Remove-Item -Recurse -Force '{0}'" -f $DataDir)

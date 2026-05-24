# Create a "Ridian Agency" shortcut on the user's Desktop.
#
# Targets Start-Ridian-Agency.bat at the repo root. Uses the bundled
# icon at desktop/assets/icon.ico if present. Safe to re-run — overwrites
# any existing shortcut of the same name.
#
# Usage (PowerShell, from anywhere):
#     powershell -NoProfile -ExecutionPolicy Bypass -File `
#         <path-to-repo>\scripts\Create-Ridian-Agency-Shortcut.ps1
#
# Or just double-click this file from File Explorer.

$ErrorActionPreference = 'Stop'

# Repo root = parent of the scripts/ folder this file lives in.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $RepoRoot 'Start-Ridian-Agency.bat'
$IconPath = Join-Path $RepoRoot 'desktop\assets\icon.ico'

if (-not (Test-Path $Launcher)) {
  Write-Host "X  Could not find $Launcher" -ForegroundColor Red
  Write-Host "   Make sure you're running this from inside the ridian-agency repo." -ForegroundColor DarkGray
  exit 1
}

$DesktopDir = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopDir 'Ridian Agency.lnk'

Write-Host ''
Write-Host '  Creating Ridian Agency desktop shortcut' -ForegroundColor White
Write-Host "  -> $ShortcutPath" -ForegroundColor DarkGray
Write-Host ''

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath       = $Launcher
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.WindowStyle      = 1            # 1 = normal
$shortcut.Description      = 'Ridian Agency — local desktop console'

if (Test-Path $IconPath) {
  $shortcut.IconLocation = "$IconPath,0"
  Write-Host "OK Using icon at $IconPath" -ForegroundColor Green
} else {
  Write-Host "!  No icon.ico found at $IconPath" -ForegroundColor Yellow
  Write-Host "   The shortcut will use the .bat file's default icon." -ForegroundColor DarkGray
  Write-Host "   To generate an icon, run:" -ForegroundColor DarkGray
  Write-Host "     .\.venv\Scripts\python.exe desktop\assets\generate_icon.py" -ForegroundColor DarkGray
}

$shortcut.Save()

Write-Host ''
Write-Host 'OK Shortcut created.' -ForegroundColor Green
Write-Host ''
Write-Host '  Next steps:' -ForegroundColor White
Write-Host '    1. Find "Ridian Agency" on your Desktop.' -ForegroundColor DarkGray
Write-Host '    2. Double-click to launch.' -ForegroundColor DarkGray
Write-Host '    3. Right-click the taskbar icon while running -> "Pin to taskbar".' -ForegroundColor DarkGray
Write-Host ''

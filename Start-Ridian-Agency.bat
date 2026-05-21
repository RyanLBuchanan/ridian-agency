@echo off
REM Ridian Agency — double-click launcher.
REM Bootstraps the PowerShell launcher that does the real work.

setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Ridian-Agency.ps1"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Launcher exited with code %EXITCODE%.
  echo Press any key to close this window.
  pause >nul
)

endlocal & exit /b %EXITCODE%

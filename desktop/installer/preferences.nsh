# electron-builder 25.1.8 calls customInit after initMultiUser and BEFORE
# installSection/uninstallOldVersion. Protect registered old location even /D moves it.
!macro customInit
  InitPluginsDir
  File /oname=$PLUGINSDIR\preserve-preferences.ps1 "${PROJECT_DIR}\installer\preserve-preferences.ps1"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\preserve-preferences.ps1" -InstallDir "$INSTDIR" -PreviousInstallDir "$perUserInstallationFolder" -Destination "$APPDATA\Ridian Operator\hidden_projects.json"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Project preferences could not be safely preserved. Installation stopped before removing the previous version. Both preference files remain available for recovery."
    Abort
  ${EndIf}
!macroend

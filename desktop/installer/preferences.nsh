# electron-builder 25.1.8 calls customInit after initMultiUser and BEFORE
# installSection/uninstallOldVersion. Protect registered old location even /D moves it.
!macro customInit
  InitPluginsDir
  File /oname=$PLUGINSDIR\preserve-preferences.ps1 "${PROJECT_DIR}\installer\preserve-preferences.ps1"
  # CI must use the same explicit sandbox store as the backend. Normal installs
  # continue to use the established roaming data directory.
  StrCpy $2 "$APPDATA\Ridian Operator"
  ReadEnvStr $3 RIDIAN_SANDBOX
  ${If} $3 != ""
    ReadEnvStr $2 RIDIAN_DATA_DIR
    ${If} $2 == ""
      Abort "Sandbox installation requires RIDIAN_DATA_DIR"
    ${EndIf}
    ${If} $2 == "$APPDATA\Ridian Operator"
      Abort "Sandbox installation cannot target the real data directory"
    ${EndIf}
  ${EndIf}
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\preserve-preferences.ps1" -InstallDir "$INSTDIR" -PreviousInstallDir "$perUserInstallationFolder" -Destination "$2\hidden_projects.json"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Project preferences could not be safely preserved. Installation stopped before removing the previous version. Both preference files remain available for recovery."
    Abort
  ${EndIf}
!macroend

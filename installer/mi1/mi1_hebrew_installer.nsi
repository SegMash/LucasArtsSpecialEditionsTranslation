; Monkey Island 1 Special Edition - Hebrew translation patch installer
; Applies VPatch updates, copies Hebrew text files, sets language, and writes an uninstaller.

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "VPatchLib.nsh"

Name "Monkey Island 1 SE - Hebrew Patch"
OutFile "..\output\MI1_Hebrew_Patch_Setup.exe"
InstallDir "C:\GOG Games\Monkey Island 1 SE"
InstallDirRegKey HKLM "Software\LucasArts\Monkey Island 1 SE" "InstallDir"
RequestExecutionLevel user
ShowInstDetails show
Unicode true

!define SETTINGS_REL "LucasArts\The Secret of Monkey Island Special Edition\Settings.ini"
!define UNINSTALLER_NAME "Uninstall MI1 Hebrew Patch.exe"
!define ENGLISH_LANGUAGE "0"

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Monkey Island 1 SE Hebrew Patch"
!define MUI_WELCOMEPAGE_TEXT "This installer patches a vanilla GOG Monkey Island 1 Special Edition installation with the Hebrew translation.$\r$\n$\r$\nThe installer updates Monkey1.pak, copies Hebrew speech/UI text files, sets the game language to Hebrew, and creates an uninstaller in the game folder."
!define MUI_PAGE_CUSTOMFUNCTION_PRE DirectoryPre
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function DirectoryPre
  IfFileExists "C:\GOG Games\Monkey Island 1 SE\MISE.exe" 0 +2
    StrCpy $INSTDIR "C:\GOG Games\Monkey Island 1 SE"
FunctionEnd

Function .onInit
  InitPluginsDir
FunctionEnd

Function un.onInit
  InitPluginsDir
FunctionEnd

Function VerifyGameFiles
  IfFileExists "$INSTDIR\MISE.exe" +3
    MessageBox MB_ICONSTOP "MISE.exe was not found in:$\n$INSTDIR$\n$\nSelect the Monkey Island 1 Special Edition installation folder."
    Abort

  IfFileExists "$INSTDIR\Monkey1.pak" +3
    MessageBox MB_ICONSTOP "Monkey1.pak was not found in:$\n$INSTDIR$\n$\nSelect the Monkey Island 1 Special Edition installation folder."
    Abort
FunctionEnd

Function un.VerifyGameFiles
  IfFileExists "$INSTDIR\MISE.exe" +3
    MessageBox MB_ICONSTOP "MISE.exe was not found in:$\n$INSTDIR"
    Abort

  IfFileExists "$INSTDIR\Monkey1.pak" +3
    MessageBox MB_ICONSTOP "Monkey1.pak was not found in:$\n$INSTDIR"
    Abort
FunctionEnd

Function UpdateSettingsIni
  ReadEnvStr $0 "APPDATA"
  StrCpy $1 "$0\${SETTINGS_REL}"
  CreateDirectory "$0\LucasArts\The Secret of Monkey Island Special Edition"
  WriteIniStr "$1" "localization" "language" "3"
  DetailPrint "Updated Settings.ini: [localization] language=3"
FunctionEnd

Function un.RestoreSettingsIni
  ReadEnvStr $0 "APPDATA"
  StrCpy $1 "$0\${SETTINGS_REL}"
  IfFileExists "$1" 0 +3
    WriteIniStr "$1" "localization" "language" "${ENGLISH_LANGUAGE}"
    DetailPrint "Updated Settings.ini: [localization] language=${ENGLISH_LANGUAGE}"
FunctionEnd

Section "Hebrew patch" SecPatch
  Call VerifyGameFiles

  ; Uncomment when MISE.exe patch is included again:
  ; DetailPrint "Patching MISE.exe..."
  ; !insertmacro VPatchFile "patches\MISE.exe.pat" "$INSTDIR\MISE.exe" "$INSTDIR\MISE.exe.tmp"

  DetailPrint "Patching Monkey1.pak (this may take a while)..."
  !insertmacro VPatchFile "patches\Monkey1.pak.pat" "$INSTDIR\Monkey1.pak" "$INSTDIR\Monkey1.pak.tmp"

  DetailPrint "Installing speech.info..."
  SetOutPath "$INSTDIR\audio"
  File "..\..\translations\m1\heb\speech.info"

  DetailPrint "Installing uiText.info..."
  SetOutPath "$INSTDIR\localization"
  File "..\..\translations\m1\heb\uiText.info"

  Call UpdateSettingsIni

  DetailPrint "Creating uninstaller..."
  WriteUninstaller "$INSTDIR\${UNINSTALLER_NAME}"

  MessageBox MB_ICONINFORMATION "Hebrew patch installed successfully.$\n$\nAn uninstaller was saved as:$\n$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Section "Uninstall"
  Call un.VerifyGameFiles

  ; Uncomment when MISE.exe patch is included again:
  ; DetailPrint "Reverting MISE.exe..."
  ; !insertmacro VPatchFile "patches\MISE.exe.revert.pat" "$INSTDIR\MISE.exe" "$INSTDIR\MISE.exe.tmp"

  DetailPrint "Reverting Monkey1.pak (this may take a while)..."
  !insertmacro VPatchFile "patches\Monkey1.pak.revert.pat" "$INSTDIR\Monkey1.pak" "$INSTDIR\Monkey1.pak.tmp"

  DetailPrint "Restoring speech.info..."
  SetOutPath "$INSTDIR\audio"
  File /oname=speech.info "..\..\translations\m1\en.speech.info"

  DetailPrint "Restoring uiText.info..."
  SetOutPath "$INSTDIR\localization"
  File /oname=uiText.info "..\..\translations\m1\en.uiText.info"

  Call un.RestoreSettingsIni

  Delete "$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Function .onInstSuccess
  DetailPrint "Installation complete."
FunctionEnd

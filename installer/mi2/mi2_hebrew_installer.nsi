; Monkey Island 2 Special Edition - Hebrew translation installer
; Copies Hebrew resource folders (fonts, localization, rooms, ui) and two binaries
; into a vanilla GOG Monkey Island 2 Special Edition installation.

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define SRC_HEB "C:\GOG Games\Monkey Island 2 SE_Heb"
!define DLL_PATH "c:\WS\LucasArtsSpecialEditionsTranslation\HebrewReorderHook\bin\Win32\Release"
!define SRC_EN  "C:\GOG Games\Monkey Island 2 SE"
Name "Monkey Island 2 SE - Hebrew Translation"
OutFile "..\output\MI2_Hebrew_Translation_Setup.exe"
InstallDirRegKey HKLM "Software\LucasArts\Monkey Island 2 SE" "InstallDir"
RequestExecutionLevel user
ShowInstDetails show
Unicode true

!define UNINSTALLER_NAME "Uninstall MI2 Hebrew Translation.exe"
!define SETTINGS_REL "LucasArts\Monkey Island 2 Special Edition\Settings.ini"

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Monkey Island 2 SE Hebrew Translation"
!define MUI_WELCOMEPAGE_TEXT "This installer installs the Hebrew translation for a vanilla GOG Monkey Island 2 Special Edition installation.$\r$\n$\r$\nIt copies the Hebrew resource folders (fonts, localization, rooms, ui) and the Hebrew loader binaries into your game folder."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  InitPluginsDir
FunctionEnd

Function un.onInit
  InitPluginsDir
FunctionEnd

; NSIS calls this automatically whenever the user changes the install directory
; (typing or browsing). Aborting here keeps the "Install" button disabled until
; a folder containing Monkey2.exe is chosen.
Function .onVerifyInstDir
  IfFileExists "$INSTDIR\Monkey2.exe" 0 +2
    Return ; valid folder -> button enabled
    Abort ; invalid folder -> button disabled
FunctionEnd

Function VerifyGameFiles
  IfFileExists "$INSTDIR\Monkey2.exe" +3
    MessageBox MB_ICONSTOP "Monkey2.exe was not found in:$\n$INSTDIR$\n$\nSelect the Monkey Island 2 Special Edition installation folder."
    Abort
FunctionEnd

Function un.VerifyGameFiles
  IfFileExists "$INSTDIR\Monkey2.exe" +3
    MessageBox MB_ICONSTOP "Monkey2.exe was not found in:$\n$INSTDIR"
    Abort
FunctionEnd

Function SetSubtitles
  ReadEnvStr $0 "APPDATA"
  StrCpy $1 "$0\${SETTINGS_REL}"
  CreateDirectory "$0\LucasArts\Monkey Island 2 Special Edition"
  WriteIniStr "$1" "audio" "subtitles" "1"
  DetailPrint "Settings.ini updated: [audio] subtitles=1"
FunctionEnd
Section "Hebrew translation" SecTranslation
  Call VerifyGameFiles

  ; Never abort the whole install because of a single locked/in-use file.
  SetOverwrite try

  DetailPrint "Installing Hebrew resource folders..."
  SetOutPath "$INSTDIR\fonts"
  File /r "${SRC_HEB}\fonts\*.*"

  SetOutPath "$INSTDIR\localization"
  File /r "${SRC_HEB}\localization\*.*"

  SetOutPath "$INSTDIR\rooms"
  File /r "${SRC_HEB}\rooms\*.*"

  SetOutPath "$INSTDIR\ui"
  File /r "${SRC_HEB}\ui\*.*"

  DetailPrint "Installing Hebrew loader binaries..."
  SetOutPath "$INSTDIR"
  File "${DLL_PATH}\version.dll"

  DetailPrint "Setting game subtitles..."
  Call SetSubtitles

  DetailPrint "Creating uninstaller..."
  WriteUninstaller "$INSTDIR\${UNINSTALLER_NAME}"

  MessageBox MB_ICONINFORMATION "Hebrew translation installed successfully.$\n$\nRun MI2HebrewLoader.exe to launch the game in Hebrew.$\n$\nAn uninstaller was saved as:$\n$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Section "Uninstall"
  Call un.VerifyGameFiles

  DetailPrint "Removing Hebrew resource folders..."
  RMDir /r "$INSTDIR\fonts"
  RMDir /r "$INSTDIR\localization"
  RMDir /r "$INSTDIR\rooms"
  RMDir /r "$INSTDIR\ui"

  DetailPrint "Removing Hebrew loader binaries..."
  Delete "$INSTDIR\version.dll"

  Delete "$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Function .onInstSuccess
  DetailPrint "Installation complete."
FunctionEnd

; Never let the installer exit silently on failure.
Function .onInstFailed
  MessageBox MB_ICONEXCLAMATION "The installation was not completed.$\n$\nPlease try again."
FunctionEnd


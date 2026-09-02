; Monkey Island 1 Special Edition - Hebrew translation installer
; Installs the Hebrew version resource folders (art, fonts), the two modified
; data files (localization\uiText.info, audio\speech.info) and the Hebrew loader
; dll (version.dll) into a vanilla GOG Monkey Island 1 Special Edition install.
; A matching uninstaller restores the original english uiText.info / speech.info.
;
; Hebrew game (source of the translated content):  C:\GOG Games\Monkey Island 1 SE_Heb
; Original game (source of the restore files):    C:\GOG Games\Monkey Island 1 SE

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define SRC_HEB "C:\GOG Games\Monkey Island 1 SE_Heb"
!define DLL_PATH "c:\WS\LucasArtsSpecialEditionsTranslation\HebrewMI1Hook\bin\Win32\Release"
!define SRC_EN  "C:\GOG Games\Monkey Island 1 SE"

Name "Monkey Island 1 SE - Hebrew Translation"
OutFile "..\output\MI1_Hebrew_Translation_Setup.exe"
InstallDirRegKey HKLM "Software\LucasArts\Monkey Island 1 SE" "InstallDir"
RequestExecutionLevel user
ShowInstDetails show
Unicode true

!define UNINSTALLER_NAME "Uninstall MI1 Hebrew Translation.exe"
!define SETTINGS_REL "LucasArts\The Secret of Monkey Island Special Edition\Settings.ini"

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Monkey Island 1 SE Hebrew Translation"
!define MUI_WELCOMEPAGE_TEXT "This installer installs the Hebrew translation for a vanilla GOG Monkey Island 1 Special Edition installation.$\r$\n$\r$\nIt copies the Hebrew resource folders (fonts, art), the translated uiText.info and speech.info, and the Hebrew loader dll (version.dll) into your game folder. An uninstaller is also written so you can restore the original English files later."
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
; a folder containing MISE.exe is chosen.
Function .onVerifyInstDir
  IfFileExists "$INSTDIR\MISE.exe" 0 +2
    Return ; valid folder -> button enabled
    Abort ; invalid folder -> button disabled
FunctionEnd

Function VerifyGameFiles
  IfFileExists "$INSTDIR\MISE.exe" +3
    MessageBox MB_ICONSTOP "MISE.exe was not found in:$\n$INSTDIR$\n$\nSelect the Monkey Island 1 Special Edition installation folder."
    Abort
FunctionEnd

Function un.VerifyGameFiles
  IfFileExists "$INSTDIR\MISE.exe" +3
    MessageBox MB_ICONSTOP "MISE.exe was not found in:$\n$INSTDIR"
    Abort
FunctionEnd

; Sets (creating as needed) %APPDATA%\LucasArts\The Secret of Monkey Island
; Special Edition\Settings.ini so the game runs in Hebrew.
Function SetLanguageHebrew
  ReadEnvStr $0 "APPDATA"
  StrCpy $1 "$0\${SETTINGS_REL}"
  CreateDirectory "$0\LucasArts\The Secret of Monkey Island Special Edition"
  WriteIniStr "$1" "localization" "language" "3"
  DetailPrint "Settings.ini updated: [localization] language=3"
FunctionEnd

; Resets the language back to English (1) in Settings.ini during uninstall.
Function un.SetLanguageEnglish
  ReadEnvStr $0 "APPDATA"
  StrCpy $1 "$0\${SETTINGS_REL}"
  CreateDirectory "$0\LucasArts\The Secret of Monkey Island Special Edition"
  WriteIniStr "$1" "localization" "language" "0"
  DetailPrint "Settings.ini updated: [localization] language=0"
FunctionEnd

Section "Hebrew translation" SecTranslation
  Call VerifyGameFiles

  ; Never abort the whole install because of a single locked/in-use file.
  SetOverwrite try

  DetailPrint "Installing Hebrew art folder..."
  SetOutPath "$INSTDIR\art"
  File /r "${SRC_HEB}\art\*.*"

  DetailPrint "Installing Hebrew fonts folder..."
  SetOutPath "$INSTDIR\fonts"
  File /r "${SRC_HEB}\fonts\*.*"

  DetailPrint "Installing Hebrew ui text..."
  SetOutPath "$INSTDIR\localization"
  File /oname=uiText.info "${SRC_HEB}\localization\uiText.info"

  DetailPrint "Installing Hebrew speech data..."
  SetOutPath "$INSTDIR\audio"
  File /oname=speech.info "${SRC_HEB}\audio\speech.info"

  DetailPrint "Installing Hebrew loader dll (version.dll)..."
  SetOutPath "$INSTDIR"
  File "${DLL_PATH}\version.dll"

  DetailPrint "Setting game language to Hebrew..."
  Call SetLanguageHebrew

  DetailPrint "Creating uninstaller..."
  WriteUninstaller "$INSTDIR\${UNINSTALLER_NAME}"

  MessageBox MB_ICONINFORMATION "Hebrew translation installed successfully.$\n$\nAn uninstaller was saved as:$\n$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Section "Uninstall"
  Call un.VerifyGameFiles

  DetailPrint "Removing Hebrew art and fonts folders..."
  RMDir /r "$INSTDIR\fonts"
  RMDir /r "$INSTDIR\art"
  
  DetailPrint "Removing Hebrew loader dll (version.dll)..."
  Delete "$INSTDIR\version.dll"
  
  DetailPrint "Restoring original uiText.info..."
  SetOverwrite try
  SetOutPath "$INSTDIR\localization"
  File /oname=uiText.info "${SRC_EN}\localization\uiText.info"

  DetailPrint "Restoring original speech.info..."
  SetOutPath "$INSTDIR\audio"
  File /oname=speech.info "${SRC_EN}\audio\speech.info"

  DetailPrint "Resetting game language to English..."
  Call un.SetLanguageEnglish

  Delete "$INSTDIR\${UNINSTALLER_NAME}"
SectionEnd

Function .onInstSuccess
  DetailPrint "Installation complete."
FunctionEnd

; Never let the installer exit silently on failure.
Function .onInstFailed
  MessageBox MB_ICONEXCLAMATION "The installation was not completed.$\n$\nPlease try again."
FunctionEnd

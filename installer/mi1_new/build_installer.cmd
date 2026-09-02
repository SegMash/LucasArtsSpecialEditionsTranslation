@echo off
setlocal

cd /d "%~dp0..\.."

set "HEB=C:\GOG Games\Monkey Island 1 SE_Heb"
set "EN=C:\GOG Games\Monkey Island 1 SE"

echo [*] Verifying Hebrew source files at %HEB%...
if not exist "%HEB%\MISE.exe" (
    echo [-] MISE.exe not found in %HEB%
    exit /b 1
)
for %%F in (audio fonts localization art) do (
    if not exist "%HEB%\%%F" (
        echo [-] Missing source folder %HEB%\%%F
        exit /b 1
    )
)
if not exist "%HEB%\version.dll" (
    echo [-] Missing source file %HEB%\version.dll
    exit /b 1
)
if not exist "%HEB%\audio\speech.info" (
    echo [-] Missing source file %HEB%\audio\speech.info
    exit /b 1
)
if not exist "%HEB%\localization\uiText.info" (
    echo [-] Missing source file %HEB%\localization\uiText.info
    exit /b 1
)

echo [*] Verifying original (English) restore files at %EN%...
if not exist "%EN%\MISE.exe" (
    echo [-] MISE.exe not found in %EN%
    exit /b 1
)
if not exist "%EN%\audio\speech.info" (
    echo [-] Missing source file %EN%\audio\speech.info
    exit /b 1
)
if not exist "%EN%\localization\uiText.info" (
    echo [-] Missing source file %EN%\localization\uiText.info
    exit /b 1
)

if not exist "installer\output" mkdir "installer\output"

echo [*] Compiling NSIS installer...
"C:\Program Files (x86)\NSIS\Bin\makensis.exe" "installer\mi1_new\mi1_hebrew_installer.nsi"
if errorlevel 1 exit /b 1

echo [+] Done: installer\output\MI1_Hebrew_Translation_Setup.exe
echo     (writes Uninstall MI1 Hebrew Translation.exe into the game folder during install)


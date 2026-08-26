@echo off
setlocal

cd /d "%~dp0..\.."

set "SRC=C:\GOG Games\Monkey Island 2 SE"

echo [*] Verifying source game files at %SRC%...
if not exist "%SRC%\Monkey2.exe" (
    echo [-] Monkey2.exe not found in %SRC%
    exit /b 1
)
for %%F in (fonts localization rooms ui) do (
    if not exist "%SRC%\%%F" (
        echo [-] Missing source folder %SRC%\%%F
        exit /b 1
    )
)
if not exist "%SRC%\HebrewReorderHook.dll" (
    echo [-] Missing source file %SRC%\HebrewReorderHook.dll
    exit /b 1
)
if not exist "%SRC%\MI2HebrewLoader.exe" (
    echo [-] Missing source file %SRC%\MI2HebrewLoader.exe
    exit /b 1
)

if not exist "installer\output" mkdir "installer\output"

echo [*] Compiling NSIS installer...
"C:\Program Files (x86)\NSIS\Bin\makensis.exe" "installer\mi2\mi2_hebrew_installer.nsi"
if errorlevel 1 exit /b 1

echo [+] Done: installer\output\MI2_Hebrew_Translation_Setup.exe
echo     (writes Uninstall MI2 Hebrew Translation.exe into the game folder during install)

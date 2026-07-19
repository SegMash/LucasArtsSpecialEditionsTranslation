@echo off
setlocal

cd /d "%~dp0..\.."

echo [*] Creating VPatch files...
python.exe .\scripts\installer\create_mi1_patches.py
if errorlevel 1 exit /b 1

if not exist "installer\mi1\patches\MISE.exe.pat" (
    echo [-] Missing installer\mi1\patches\MISE.exe.pat
    exit /b 1
)
if not exist "installer\mi1\patches\Monkey1.pak.pat" (
    echo [-] Missing installer\mi1\patches\Monkey1.pak.pat
    exit /b 1
)
if not exist "installer\mi1\patches\MISE.exe.revert.pat" (
    echo [-] Missing installer\mi1\patches\MISE.exe.revert.pat
    exit /b 1
)
if not exist "installer\mi1\patches\Monkey1.pak.revert.pat" (
    echo [-] Missing installer\mi1\patches\Monkey1.pak.revert.pat
    exit /b 1
)
if not exist "translations\m1\heb\speech.info" (
    echo [-] Missing translations\m1\heb\speech.info
    exit /b 1
)
if not exist "translations\m1\heb\uiText.info" (
    echo [-] Missing translations\m1\heb\uiText.info
    exit /b 1
)
if not exist "translations\m1\en.speech.info" (
    echo [-] Missing translations\m1\en.speech.info
    exit /b 1
)
if not exist "translations\m1\en.uiText.info" (
    echo [-] Missing translations\m1\en.uiText.info
    exit /b 1
)

if not exist "installer\output" mkdir "installer\output"

echo [*] Compiling NSIS installer...
makensis.exe "installer\mi1\mi1_hebrew_installer.nsi"
if errorlevel 1 exit /b 1

echo [+] Done: installer\output\MI1_Hebrew_Patch_Setup.exe
echo     (writes Uninstall MI1 Hebrew Patch.exe into the game folder during install)

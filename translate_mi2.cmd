@echo off
setlocal EnableDelayedExpansion
set "GAME_DIR=C:\GOG Games\Monkey Island 2 SE"
set "FONT_SRC=%GAME_DIR%\quickbms\extracted_orig\fonts"
set "LOCAL_DEST=%GAME_DIR%\quickbms\extracted\localization"
copy /y "%LOCAL_DEST%\en.speech.info"  "%LOCAL_DEST%\en.speech.info.bak"  
copy /y "%LOCAL_DEST%\en.uitext.info"  "%LOCAL_DEST%\en.uitext.info.bak"  
python scripts\text\inject_translation.py  "%LOCAL_DEST%"  --he-dir .\translations\mi2  --report

mkdir fonts_mi2
for %%A in ("%FONT_SRC%\*.font") do (
    set "F=%%~nA"
    set "skip=0"
    if /i "!F!"=="MinisterT_b_24" set skip=1
    if /i "!F!"=="MinisterT_bo_24" set skip=1
    if /i "!F!"=="MinisterT_24" set skip=1
    if /i "!F!"=="MinisterT_bo_20" set skip=1
    if /i "!F!"=="MinisterT_20" set skip=1
    if /i "!F!"=="MinisterT_16" set skip=1
    if "!skip!"=="0" (
        if exist "fonts_mi2\!F!" rmdir /s /q "fonts_mi2\!F!"
        del /q /f /s "fonts_mi2\!F!.*" 2>nul
        :: העתקת קובצי המקור הנקיים
        copy /y "%FONT_SRC%\!F!.*" fonts_mi2\
        :: הרצת סקריפט הפייתון לבניית הפונט בעברית
        python.exe .\scripts\fonts\build_hebrew_font.py mi2 .\fonts_mi2 !F! --hebrew-gap 1
        :: העתקת הפונט הבנוי החדש אל תיקיית היעד של המשחק
        copy /y "fonts_mi2\!F!.*" "%GAME_DIR%\quickbms\extracted\fonts\"
    )
)
python scripts\reverse-engineering\apply_reverse_patch.py "%GAME_DIR%\Monkey2.exe" --apply

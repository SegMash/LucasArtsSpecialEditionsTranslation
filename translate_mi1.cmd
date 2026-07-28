REM MinisterT_b_24 - BAD
REM MinisterT_bo_20 - BAD
REM MinisterT_20 - BAD
REM MinisterT_16 - BAD
setlocal EnableDelayedExpansion
set "FONT_SRC=C:\GOG Games\Monkey Island 1 SE\quickbms\extracted_orig\fonts"
python.exe .\scripts\text\inject_translation_mi1.py --reverse-for-ltr --txt .\translations\m1\heb\he.speech.txt --bin .\translations\m1\en.speech.info --out .\translations\m1\heb\speech.info --start 0x310 --jump 0x530
python.exe .\scripts\text\inject_translation_mi1.py --reverse-for-ltr --txt .\translations\m1\heb\he.uitext.txt --bin .\translations\m1\en.uiText.info --out .\translations\m1\heb\uiText.info --start 0x400 --jump 0x600
copy /y .\translations\m1\heb\speech.info "C:\GOG Games\Monkey Island 1 SE\audio\"
copy /y .\translations\m1\heb\uiText.info "C:\GOG Games\Monkey Island 1 SE\localization\"
mkdir fonts_mi1
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
        if exist "fonts_mi1\!F!" rmdir /s /q "fonts_mi1\!F!"
        del /q /f /s "fonts_mi1\!F!.*" 2>nul
        :: העתקת קובצי המקור הנקיים
        copy /y "%FONT_SRC%\!F!.*" fonts_mi1\
        :: הרצת סקריפט הפייתון לבניית הפונט בעברית
        python.exe .\scripts\fonts\build_hebrew_font.py .\fonts_mi1 !F! --hebrew-gap 1 --ttf .\frank.ttf --ttf-bold .\frank.ttf
        :: העתקת הפונט הבנוי החדש אל תיקיית היעד של המשחק
        copy /y "fonts_mi1\!F!.*" "C:\GOG Games\Monkey Island 1 SE\quickbms\extracted\fonts\"
    )
)
endlocal
cd "C:\GOG Games\Monkey Island 1 SE\quickbms"
quickbms.exe -w -r -r -r monkey_island_2.bms ..\Monkey1.pak extracted
cd "C:\WS\LucasArtsSpecialEditionsTranslation"
python.exe .\scripts\reverse-engineering\apply_mi1_verbline_rtl.py
python.exe .\scripts\reverse-engineering\apply_mi1_merge_to_object.py
python.exe .\scripts\reverse-engineering\apply_mi1_dynamic_text_translate.py
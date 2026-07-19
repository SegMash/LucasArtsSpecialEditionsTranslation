python.exe .\scripts\text\inject_translation_mi1.py --txt .\translations\m1\heb\he.speech.txt --bin .\translations\m1\en.speech.info --out .\translations\m1\heb\speech.info --start 0x310 --jump 0x530
python.exe .\scripts\text\inject_translation_mi1.py --reverse-for-ltr --txt .\translations\m1\heb\he.uitext.txt --bin .\translations\m1\en.uiText.info --out .\translations\m1\heb\uiText.info --start 0x400 --jump 0x600
copy /y .\translations\m1\heb\speech.info "C:\GOG Games\Monkey Island 1 SE\audio\"
copy /y .\translations\m1\heb\uiText.info "C:\GOG Games\Monkey Island 1 SE\localization\"
rem for %%F in (MinisterT_b_36 MinisterT_bo_36 MinisterT_36 MinisterT_b_48 MinisterT_b_24 MinisterT_bo_20) do (
for %%F in (MinisterT_b_36 MinisterT_bo_36 MinisterT_36 MinisterT_b_48) do (
    if exist "fonts_mi1\%%F" rmdir /s /q "fonts_mi1\%%F"
    del /q /f /s "fonts_mi1\%%F.*" 2>nul
    :: העתקת קובצי המקור הנקיים
    copy /y "C:\GOG Games\Monkey Island 1 SE\quickbms\extracted_orig\fonts\%%F.*" fonts_mi1\
    :: הרצת סקריפט הפייתון לבניית הפונט בעברית
    python.exe .\scripts\fonts\build_hebrew_font.py .\fonts_mi1 %%F --hebrew-gap 1 --ttf .\frank.ttf --ttf-bold .\frank.ttf
    :: העתקת הפונט הבנוי החדש אל תיקיית היעד של המשחק
    copy /y "fonts_mi1\%%F.*" "C:\GOG Games\Monkey Island 1 SE\quickbms\extracted\fonts\"
)
cd "C:\GOG Games\Monkey Island 1 SE\quickbms"
quickbms.exe -w -r -r -r monkey_island_2.bms ..\Monkey1.pak extracted
cd "C:\WS\LucasArtsSpecialEditionsTranslation"
python.exe .\scripts\reverse-engineering\apply_mi1_patch.py
REM MinisterT_b_24 - BAD
REM MinisterT_bo_20 - BAD
REM MinisterT_20 - BAD
REM MinisterT_16 - BAD
REM for %%F in (MinisterT_b_36 MinisterT_bo_36 MinisterT_36 MinisterT_b_48) do (
REM for %%F in (MinisterT_b_30 MinisterT_b_20 MinisterT_b_16) do (
REM for %%F in (MinisterT_36 MinisterT_32 MinisterT_30) do (
REM for %%F in (MinisterT_b_32 MinisterT_bo_32 MinisterT_24) do (
REM for %%F in (MinisterT_b_45 MinisterT_bo_45 MinisterT_45) do (
REM for %%F in (MinisterT_64 MinisterT_b_55 MinisterT_b_96 MinisterT_bo_24 MinisterT_bo_30) do (
rem for %%F in (MinisterT_bo_48 MinisterT_bo_45 MinisterT_bo_96) do (
REM for %%F in (MinisterT_bo_55 MinisterT_42 MinisterT_72) do (
REM for %%F in (MinisterT_b_64 MinisterT_b_72 MinisterT_b_96) do (
REM for %%F in (MinisterT_bo_16 MinisterT_48 MinisterT_55 MinisterT_96) do (
rem for %%F in (Arial_bo_13 Arial_bo_18 Arial_bo_27) do (
rem for %%F in (MinisterT_bo_42 MinisterT_bo_64 MinisterT_bo_72) do (
rem for %%F in (MinisterT_bo_24) do (
rem for %%F in (MinisterT_b_24 MinisterT_bo_20 MinisterT_20) do (
for %%F in (MinisterT_b_24 MinisterT_bo_20 MinisterT_20) do (
    if exist "fonts_mi1\%%F" rmdir /s /q "fonts_mi1\%%F"
    del /q /f /s "fonts_mi1\%%F.*" 2>nul
    :: העתקת קובצי המקור הנקיים
    copy /y "C:\GOG Games\Monkey Island 1 SE\quickbms\extracted_orig\fonts\%%F.*" fonts_mi1\
    :: הרצת סקריפט הפייתון לבניית הפונט בעברית
    :: python.exe .\scripts\fonts\build_hebrew_font.py .\fonts_mi1 %%F --hebrew-gap 1 --ttf .\frank.ttf --ttf-bold .\frank.ttf
    python.exe .\scripts\fonts\build_hebrew_font.py .\fonts_mi1 %%F --hebrew-gap 1 --max-fraction 0.7 --ttf .\frank.ttf --ttf-bold .\frank.ttf
    :: העתקת הפונט הבנוי החדש אל תיקיית היעד של המשחק
    copy /y "fonts_mi1\%%F.*" "C:\GOG Games\Monkey Island 1 SE\quickbms\extracted\fonts\"
)
cd "C:\GOG Games\Monkey Island 1 SE\quickbms"
quickbms.exe -w -r -r -r monkey_island_2.bms ..\Monkey1.pak extracted
cd "C:\WS\LucasArtsSpecialEditionsTranslation"

@echo off
setlocal

:: ============================================================
::  build_hebrew_font.bat
::  Rebuild Monkey Island 2 SE fonts with Hebrew letter support
::
::  Usage (single font):
::    build_hebrew_font.bat <fonts_dir> <font_name>
::    e.g.: build_hebrew_font.bat "quickbms\output\fonts" MinisterT_24
::
::  Usage (all fonts):
::    build_hebrew_font.bat <fonts_dir>
::    e.g.: build_hebrew_font.bat "quickbms\output\fonts"
::
::  Extra options are passed through to build_hebrew_font.py:
::    --output-dir DIR   where to write rebuilt .font + .png
::    --ttf PATH         Hebrew TTF override (regular / italic)
::    --ttf-bold PATH    Hebrew TTF override (bold / bold-oblique)
::    --max-fraction N   letter height fraction (default 0.70)
::    --hebrew-gap N     extra advance pixels after each Hebrew letter
::                       (default 1, recommended: 1 — Hebrew letters are more
::                        "square" than Latin and need extra spacing)
::    --align MODE       'left' (default) or 'center'.
::                       left  : pixels at col 0, PNG width = natural letter width
::                               -> rebuild_manifest correctly updates advance_x.
::                       center: letter centred in original slot — PNG width unchanged
::                               -> advance_x is NOT recalculated (old spacing kept).
::    --border-mode M    For border fonts (name contains _bo_) — where to draw the
::                       1-pixel black contour:
::                       outer (default): halo around the letter (body stays bright).
::                       inner          : paint the outermost letter pixels black
::                                        (same footprint, thinner letter body).
::    --no-quantize      Disable the default 5-level RGBA quantization
::                       (faint/dark/mid/light/solid) that matches the
::                       original font atlas's colour encoding.
::    --clean            Delete the glyph subfolder after each font is built.
::    --dry-run          preview without writing files
::
::  For the full list, run:
::    build_hebrew_font.bat --help
:: ============================================================

if "%~1"=="" (
    echo Usage: build_hebrew_font.bat ^<fonts_dir^> [font_name] [options]
    echo.
    echo Example ^(single font^):
    echo   build_hebrew_font.bat "quickbms\output\fonts" MinisterT_24 --hebrew-gap 1
    echo.
    echo Example ^(all fonts^):
    echo   build_hebrew_font.bat "quickbms\output\fonts" --hebrew-gap 1
    echo.
    echo Common options:
    echo   --output-dir DIR     where to write rebuilt .font + .png
    echo   --ttf       PATH     Hebrew TTF ^(regular/italic^)
    echo   --ttf-bold  PATH     Hebrew TTF ^(bold/bold-oblique^)
    echo   --hebrew-gap N       extra advance pixels after each Hebrew glyph
    echo                        ^(default 1, recommended: 1^)
    echo   --max-fraction N     letter height as fraction of cell ^(default 0.70^)
    echo   --align     MODE     'left' ^(default^) or 'center'
    echo   --border-mode MODE   'outer' ^(default^) or 'inner' — applies to *_bo_* fonts
    echo   --clean              remove intermediate glyph folder when done
    echo   --dry-run            preview without writing files
    echo.
    echo Run with --help for the complete list of options.
    exit /b 1
)

:: Locate the scripts folder relative to this batch file
set "SCRIPTS=%~dp0scripts\fonts"

python -X utf8 "%SCRIPTS%\build_hebrew_font.py" %*

if %errorlevel% neq 0 (
    echo.
    echo Build FAILED. See errors above.
    exit /b %errorlevel%
)

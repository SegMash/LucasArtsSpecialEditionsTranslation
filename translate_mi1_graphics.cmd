@echo off
setlocal EnableDelayedExpansion
REM Copy english help menu images to german help menu
set "SOURCE=C:\GOG Games\Monkey Island 1 SE\quickbms\extracted_orig\art\ui\helpmenu\en\"
set "DEST=C:\GOG Games\Monkey Island 1 SE\quickbms\extracted\art\ui\helpmenu\ge\"
for %%F in ("%SOURCE%\*") do (
    set "filename=%%~nxF"
    set "newname=!filename:_en_=_ge_!"
    copy "%%F" "%DEST%\!newname!" >nul
)
REM Generate hebrew dxt files based on png files.
set "PNG_SRC_DIR=images\processed"
set "DXT_TARGET_DIR=C:\GOG Games\Monkey Island 1 SE\quickbms\extracted\art\rooms\images
for %%A in ("%PNG_SRC_DIR%\*.png") do (
    set "F=%%~nA"
    set "target_dir="
    set "name="
    call :PARSE_STRING "!F!"
    
    set "chunks=0"
    if "!name!"=="layer0" (
        set "chunks=4"
    )
    python.exe .\scripts\image\create_dxt_files.py --input "%PNG_SRC_DIR%\!F!.png" --name !name! --chunks !chunks! --target "%DXT_TARGET_DIR%\!target_dir!"
        
)
endlocal

:PARSE_STRING
set "str=%~1"
for %%B in ("%str:__=" "%") do (
    set "target_dir=!name!"
    set "name=%%~B"
)
set "target_dir=!target_dir:"=!"
goto :eof
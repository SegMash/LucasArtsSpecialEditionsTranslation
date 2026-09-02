@echo off
setlocal EnableDelayedExpansion
REM Copy english help menu images to german help menu
set "SOURCE=C:\GOG Games\Monkey Island 1 SE\quickbms\extracted_orig\art\ui\helpmenu\en\"
set "DEST=C:\GOG Games\Monkey Island 1 SE\art\ui\helpmenu\ge\"
if not exist "%SOURCE%" (
    echo Source directory "%SOURCE%" does not exist.
    exit /b 1
)
if not exist "%DEST%" (
    echo Destination directory "%DEST%" does not exist. Creating it.
    mkdir "%DEST%"
)  
for %%F in ("%SOURCE%\*") do (
    set "filename=%%~nxF"
    set "newname=!filename:_en_=_ge_!"
    copy "%%F" "%DEST%\!newname!" >nul
)

REM Generate hebrew dxt files based on png files.
set "PNG_SRC_DIR=images\processed"
set "DXT_TARGET_DIR=C:\GOG Games\Monkey Island 1 SE\art\rooms\images
if not exist "%PNG_SRC_DIR%" (
    echo Source directory "%PNG_SRC_DIR%" does not exist.
    exit /b 1
)
if not exist "%DXT_TARGET_DIR%" (
    echo Destination directory "%DXT_TARGET_DIR%" does not exist. Creating it.
    mkdir "%DXT_TARGET_DIR%"
)
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
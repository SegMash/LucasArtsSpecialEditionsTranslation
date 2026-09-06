@echo off
setlocal EnableDelayedExpansion
REM Generate hebrew dxt files based on png files.
set "PNG_SRC_DIR=images_mi2\processed"
set "DXT_TARGET_DIR=C:\GOG Games\Monkey Island 2 SE_Heb\ui
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__maphelpmenu__en__en_maphelpmenu_bundle_pk_a00.png" --name en_maphelpmenu_bundle_pk_a00 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\maphelpmenu\en"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__icons__en_mapsaveloadmenu_bundle_pk_a01.png" --name en_mapsaveloadmenu_bundle_pk_a01 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\icons"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__mapsaveloadmenu__en_mapsaveloadmenu_bundle_pk_a00.png" --name en_mapsaveloadmenu_bundle_pk_a00 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\mapsaveloadmenu"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__maphelpmenu__settings__en_mapbonusfeaturesmenu_bundle_pk_a00.png" --name en_mapbonusfeaturesmenu_bundle_pk_a00 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\maphelpmenu\settings"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__mapconceptartmenu__en_mapconceptartmenu_bundle_pk_a01.png" --name en_mapconceptartmenu_bundle_pk_a01 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\mapconceptartmenu"
exit
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__backgroundmapmenu__en_mapmainmenu_bundle_pk_a01.png" --name en_mapmainmenu_bundle_pk_a01 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\backgroundmapmenu"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__backgroundmapmenu__en_mapmainmenu_bundle_pk_a00.png" --name en_mapmainmenu_bundle_pk_a00 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\backgroundmapmenu"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__mapmainmenu__en__en_mapmainmenu_bundle_pk_a02.png" --name en_mapmainmenu_bundle_pk_a02 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\mapmainmenu\en"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__mappausemenu__en__en_mappausemenu_bundle_pk_a01.png" --name en_mappausemenu_bundle_pk_a01 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\mappausemenu\en"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__ui__mappausemenu__en__en_mappausemenu_bundle_pk_a00.png" --name en_mappausemenu_bundle_pk_a00 --chunks 0 --gzip --target "%DXT_TARGET_DIR%\mappausemenu\en"

set "DXT_TARGET_DIR=C:\GOG Games\Monkey Island 2 SE_Heb\rooms\images"
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__rooms__images__2_scabb-isl__layer1.png" --name layer1 --chunks 2 --gzip --target "%DXT_TARGET_DIR%\2_scabb-isl"


set "DXT_TARGET_DIR=C:\GOG Games\Monkey Island 2 SE_Heb\rooms\images
python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\images_mi2__en__rooms__images__2_scabb-isl__layer1.png" --name layer1 --chunks 2 --gzip --target "%DXT_TARGET_DIR%\2_scabb-isl"
for %%A in ("%PNG_SRC_DIR%\*_part*.png") do (
    set "F=%%~nA"
    set "target_dir="
    set "name="
    call :PARSE_STRING "!F!"
    
    set "chunks=0"
    if "!name!"=="layer0" (
        set "chunks=2"
    )
    python.exe .\scripts\image\create_dxt_files_mi2.py --input "%PNG_SRC_DIR%\!F!.png" --name !name! --chunks !chunks! --chunk-width 1024 --chunk-height 1024 --yx-coords  --overlap 4 --gzip --target "%DXT_TARGET_DIR%\!target_dir!"
        
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
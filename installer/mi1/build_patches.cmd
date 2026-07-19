@echo off
cd /d "%~dp0..\.."
python.exe .\scripts\installer\create_mi1_patches.py
exit /b %ERRORLEVEL%

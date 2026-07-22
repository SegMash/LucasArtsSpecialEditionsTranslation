python.exe .\scripts\text\inject_translation_mi1.py --txt .\translations\m1\heb\he.speech.txt --bin .\translations\m1\en.speech.info --out .\translations\m1\heb\speech.info --start 0x310 --jump 0x530
python.exe .\scripts\text\inject_translation_mi1.py --reverse-for-ltr --txt .\translations\m1\heb\he.uitext.txt --bin .\translations\m1\en.uiText.info --out .\translations\m1\heb\uiText.info --start 0x400 --jump 0x600
copy /y .\translations\m1\heb\speech.info "C:\GOG Games\Monkey Island 1 SE\audio\"
copy /y .\translations\m1\heb\uiText.info "C:\GOG Games\Monkey Island 1 SE\localization\"
REM python.exe .\scripts\reverse-engineering\apply_mi1_patch.py
# LucasArts Special Editions — Hebrew Translation

Tools and assets for translating LucasArts *Special Edition* re-releases
(currently **Monkey Island 2: Special Edition**) into Hebrew, end-to-end:

- **rendering Hebrew letters into the game's bitmap fonts**,
- **replacing every dialog and UI string with its Hebrew translation**, and
- **patching the executable so Hebrew text reads right-to-left on screen**.

Each part is self-contained — you can run them independently — but together
they produce a fully-Hebrew playable build.

---

## At a glance

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PRE-REQUISITE                                                           │
│  Extract the game's resource archive (e.g. monkey2.pak) into a folder   │
│  using QuickBMS, MISE Explorer 0.6, or any other compatible tool.       │
└──────────────────────────────────────────────────────────────────────────┘
                ┌───────────────────────────────┐
                │ PART 1 — Hebrew Fonts          │
                │ scripts/fonts/                 │
                │ Render Hebrew glyphs into the  │
                │ .font + atlas .png pairs.      │
                └───────────────────────────────┘

                ┌───────────────────────────────┐
                │ PART 2 — Replace Text          │
                │ scripts/text/                  │
                │ Translate every dialog / UI    │
                │ string in the .info files.     │
                └───────────────────────────────┘

                ┌───────────────────────────────┐
                │ PART 3 — Executable Patch      │
                │ scripts/reverse-engineering/   │
                │ Make the engine render Hebrew  │
                │ right-to-left + fix Israeli    │
                │ date / time / percent layout.  │
                └───────────────────────────────┘
                              ▲
                              │
              Re-pack the modified files back into monkey2.pak
                       (same tool used to extract)
```

---

## Pre-requisite — extract the resource archive

The game ships its assets in a single `.pak` archive (`monkey2.pak` for
Monkey Island 2 SE). All scripts in this project work on the **already
extracted** files. Use any tool that can extract this archive, for example:

- **QuickBMS** with the appropriate `.bms` script.
- **MISE Explorer 0.6** (community tool for LucasArts Special Editions).

After extraction you should see (at minimum):

```
extracted/
  fonts/                    <- used by Part 1
    MinisterT_24.font
    MinisterT_24.png
    ... (one .font + .png per font/size variant)
  localization/             <- used by Part 2
    en.speech.info
    en.uitext.info
    ... (per-language pairs)
  Monkey2.exe               <- used by Part 3
```

> **Back up the extracted folder before you start.**
> All scripts overwrite their inputs in place unless you explicitly point
> them at a separate output directory. Keep a clean copy of the originals
> (and the `.bak` files described below).

---

## Python prerequisites

Python ≥ 3.9 is required. Install dependencies once:

```sh
pip install Pillow numpy
```

Hebrew TTF fonts are also needed for Part 1 — see `scripts/fonts/README.md`
for details (the default expects the Frank Ruehl fonts that ship with
Windows; any Hebrew TTF works with `--ttf` / `--ttf-bold`).

---

## Part 1 — Create Hebrew fonts

Replace the European-character glyphs in each `.font` + `.png` pair with
rendered Hebrew letters, then write new files the game engine accepts.

### Quick start (Windows) — `build_hebrew_font.bat`

A one-shot batch wrapper lives in the **repo root** that runs the full
4-step font pipeline for you. It forwards every flag straight to
`scripts/fonts/build_hebrew_font.py`, so it works from anywhere — your
working directory does not have to be the game directory.

**Rebuild every font in a folder (recommended):**

```bat
build_hebrew_font.bat  "C:\extracted\fonts.bak"  --output-dir "C:\extracted\fonts"  --hebrew-gap 1
```

**Rebuild a single font** (useful when iterating on one variant):

```bat
build_hebrew_font.bat  "C:\extracted\fonts.bak"  MinisterT_24  --output-dir "C:\extracted\fonts"  --hebrew-gap 1
```

> **Recommended flag — `--hebrew-gap 1`**
> Hebrew letters tend to be more **"square"** than their Latin
> counterparts (their visual mass extends nearly all the way to the
> bounding box on both sides), so they need a little extra advance to
> keep words readable. `--hebrew-gap 1` adds 1 pixel of extra advance
> after every Hebrew glyph (`advance_x = letter_width + 1`). Increase
> further if your TTF still feels cramped; lower only if you want a
> tighter look.

Other useful flags (passed straight through to the orchestrator):

| Flag | Meaning |
|---|---|
| `--output-dir DIR` | Where to write rebuilt `.font` + `.png` files. Defaults to overwriting the input folder. |
| `--ttf PATH` / `--ttf-bold PATH` | Override the default Hebrew TTF (Frank Ruehl on Windows) with any other Hebrew font. |
| `--max-fraction N` | Cap Hebrew letter height at N × cell height (default `0.70`). Lower it if descenders clip. |
| `--align left\|center` | Glyph placement inside its slot. `left` (default) matches the original atlas convention. |
| `--clean` | Delete the intermediate glyph subfolder after each font is built. |
| `--dry-run` | Print what would happen without writing anything. |

`build_hebrew_font.bat` with no arguments prints a short usage reminder.

### Non-Windows / direct Python call

The bat file is just a thin wrapper around:

```sh
python scripts/fonts/build_hebrew_font.py  <fonts_dir> [font_name] [options]
```

Use this form on macOS / Linux, or anywhere else without `cmd.exe`.

### Behind the scenes

**See [`scripts/fonts/README.md`](scripts/fonts/README.md)** for the full
4-step pipeline (`parse_font` → `generate_hebrew_glyphs` →
`rebuild_manifest` → `create_font`), customisation options, per-script
references, and troubleshooting.

---

## Part 2 — Replace text

The game stores all dialog and UI strings in two binary `.info` files:

| File | Contains |
|---|---|
| `en.speech.info` | Spoken-line text (8 700+ records, one per voice line) |
| `en.uitext.info` | Menus, labels, tooltips (~1 270 records, KEY ↔ DISPLAY pairs) |

Translating them is a **four-step pipeline** that keeps an English source
and its Hebrew translation perfectly line-synchronised so you can
hand-edit any single line without breaking record alignment.

### Step 2.1 — Extract the original English text

```sh
python scripts/text/extract_text.py  <loc_dir>  -o translations
```

Reads `en.speech.info` and `en.uitext.info` and writes:

```
translations/
  en.speech.txt        <- one English message per line, in record order
  en.uitext.txt        <- one English DISPLAY string per line
```

Newlines inside a message are kept as literal `\n` / `\r` so every message
occupies exactly **one line** of the output file. The line index is the
record index in the `.info` file — that synchronisation is the backbone of
the whole pipeline.

### Step 2.2 — Build line-synced Hebrew files

```sh
python scripts/text/build_translation.py  --report
```

Reads `translations/en.speech.txt` + `translations/en.uitext.txt` and the
mapping files:

| Mapping file | Encoding | Purpose |
|---|---|---|
| `translations/mapping.txt` | Windows-1255 | Main `english === hebrew` dictionary |
| `translations/*_mapping.txt` | UTF-8 | Auto-discovered secondary mappings, exact-match fallback (e.g. `extra_mapping.txt`, `uit_text_mapping.txt`, …) |

All files in `translations/` whose name matches `*_mapping.txt` are
loaded automatically, in **alphabetical order**, after the primary
`mapping.txt`.  On duplicate keys the first-loaded file wins, so name new
files to control priority (or pass `--extra-mapping FILE [FILE …]` to
override the auto-discovery with an explicit list).

For each English line the script runs a 13-step lookup cascade (handles
case / spaces / quotes / backticks / embedded `{tokens}` / multi-line
strings / etc.) and writes:

```
translations/mi2/
  he.speech.txt        <- line N = Hebrew translation of line N in en.speech.txt
  he.uitext.txt        <- line N = Hebrew translation of line N in en.uitext.txt
```

If no translation is found, the English line is written verbatim — this
keeps line counts perfectly synced and makes "untranslated" lines easy to
spot (and to fix in step 2.3).

Pass `--report` to also write `translations/missing_from_build.txt` listing
every untranslated line, grouped by source file.

#### Refreshing after the first hand-edit pass — `--merge`

Once you've started hand-editing `mi2/he.*.txt`, a plain re-run would
overwrite those edits.  Pass `--merge` (mutually exclusive with
`--override`) to refresh **only the lines that are still English
fallback**.  Any line that already differs from its English source is
treated as a hand-edit and preserved verbatim:

```sh
python scripts/text/build_translation.py  --out-dir translations/mi2  --merge  --report
```

The run prints a `… hand-edits preserved` counter alongside the usual
translated/fallback stats so you can see at a glance what changed.  Use
this whenever you add a new mapping entry or a whole new `*_mapping.txt`
file and want it picked up without losing manual work.

### Step 2.3 — Hand-edit the Hebrew files (optional but recommended)

`mi2/he.speech.txt` and `mi2/he.uitext.txt` are the **ground truth** for
the next step. Edit them directly to:

- fill in lines that the mapping couldn't translate,
- pick context-appropriate translations for ambiguous phrases (the
  English `"Excuse me."` may need to be `סלח לי`, `סלחו לי`, `סליחה`,
  or `סלחי לי` depending on speaker / situation — each occurrence is its
  own line in the file, so each can be different),
- tweak word choice or wording without worrying about whether other
  occurrences will be affected.

Keep the line count unchanged. Newlines inside a message must stay
escaped as `\n` / `\r` so each message still occupies one file line.

### Step 2.4 — Inject Hebrew text into the `.info` files

Before the first injection, copy the original (factory) `.info` files as
backups; the injector refuses to run without them:

```sh
copy extracted/localization/en.speech.info  extracted/localization/en.speech.info.bak
copy extracted/localization/en.uitext.info  extracted/localization/en.uitext.info.bak
```

Then:

```sh
python scripts/text/inject_translation.py  <loc_dir>  --report
```

The injector:

1. Restores `en.speech.info` / `en.uitext.info` from their `.bak` files
   (so it always starts from a clean slate, never compounding edits).
2. Reads `mi2/he.speech.txt` and `mi2/he.uitext.txt`.
3. For every record:
   - If the Hebrew line equals the English line (verbatim fallback) →
     encodes the original ASCII reversed, so the engine's RTL patch
     renders it left-to-right.
   - Otherwise → encodes Hebrew via the custom single-byte encoding used
     by the game (`scripts/fonts/hebrew_mapping.py`).
4. Rebuilds the `.info` files with **recomputed pointers** — no
   truncation, no padding. Hebrew lines longer than the original English
   are handled correctly in both `.speech.info` and `.uitext.info`.

Re-pack the modified `localization/` folder back into the game's `.pak`.

> **Iterate freely.** Once your `.bak` files are in place, you can edit
> `he.speech.txt` / `he.uitext.txt`, rerun the injector, repack, and try
> it in-game. Repeat as often as needed.

---

## Part 3 — Executable patch (RTL support)

LucasArts Special Edition engines draw every character left-to-right, so
naïvely-stored Hebrew comes out mirrored. This part patches `Monkey2.exe`
to:

1. **Intercept every `DrawString` call** and byte-reverse the input so
   Hebrew letters display in the correct right-to-left order.
2. **Skip the reversal for strings that contain digits** (date / time /
   percentage), so save-screen labels like `22:15:47`, `12/05/2026`, and
   `37%` stay readable.
3. **Swap a handful of save/load format strings** in place so the value
   appears before the Hebrew label (`{0} {1}%` → `{1}% {0}`, etc.), and
   convert the date format from US `MM-DD-YYYY` to Israeli `DD-MM-YYYY`.

All patches are length-preserving and idempotent.

### Apply / restore / diagnose

The script takes the **full path to the target EXE** followed by exactly
one action flag:

```sh
python scripts/reverse-engineering/apply_reverse_patch.py "C:/Games/MI2/Monkey2.exe" --apply
python scripts/reverse-engineering/apply_reverse_patch.py "C:/Games/MI2/Monkey2.exe" --restore
python scripts/reverse-engineering/apply_reverse_patch.py "C:/Games/MI2/Monkey2.exe" --diagnose
```

`--apply`, `--restore`, and `--diagnose` are mutually exclusive and one
of them is required.

Add `--force` (only valid together with `--apply`) to overwrite even
when the bytes at a known address don't match the expected pattern
(e.g. a partial / unknown previous patch).

```sh
python scripts/reverse-engineering/apply_reverse_patch.py "C:/Games/MI2/Monkey2.exe" --apply --force
```

The script edits the given EXE in place — **back it up before the first
run**.  The hard-coded addresses (DrawString, wrapper cave, ring buffer,
format strings) are tuned for **Monkey Island 2 SE**; patching another
LucasArts SE title requires re-discovering those VAs in the new EXE.

---

## End-to-end workflow

```sh
# 0. Extract the pak archive (QuickBMS, MISE Explorer 0.6, ...)
quickbms scripts/quickbms.bms  game/monkey2.pak  extracted/

# 1. Back up everything you're about to modify
xcopy /E /I  extracted/fonts          extracted/fonts.bak
xcopy /E /I  extracted/localization   extracted/localization.bak
copy         extracted/localization/en.speech.info   extracted/localization/en.speech.info.bak
copy         extracted/localization/en.uitext.info   extracted/localization/en.uitext.info.bak
copy         extracted/Monkey2.exe                   extracted/Monkey2.exe.bak

# 2. Part 1 — Hebrew fonts  (Windows: use the bat wrapper at repo root)
build_hebrew_font.bat  extracted/fonts.bak  --output-dir extracted/fonts  --hebrew-gap 1
#    macOS / Linux equivalent:
#    python scripts/fonts/build_hebrew_font.py  extracted/fonts.bak  --output-dir extracted/fonts  --hebrew-gap 1

# 3. Part 2 — text
python scripts/text/extract_text.py        extracted/localization  -o translations
python scripts/text/build_translation.py   --out-dir translations/mi2  --report
#    (optionally hand-edit translations/mi2/he.*.txt here)
#    (after adding new mapping entries, re-run with --merge to refresh
#     only the untranslated lines while preserving your hand-edits:)
#    python scripts/text/build_translation.py   --out-dir translations/mi2  --merge  --report
python scripts/text/inject_translation.py  extracted/localization  --report

# 4. Part 3 — EXE patch
python scripts/reverse-engineering/apply_reverse_patch.py  extracted/Monkey2.exe  --apply

# 5. Re-pack the modified files back into monkey2.pak
#    (use whichever repacker matches your extraction tool)
```

---

## Project layout

```
.
├─ README.md                          <- you are here
├─ build_hebrew_font.bat              <- Windows convenience wrapper for Part 1
├─ scripts/
│  ├─ fonts/                          <- Part 1
│  │  ├─ README.md                    (font pipeline details)
│  │  ├─ build_hebrew_font.py         (orchestrator)
│  │  ├─ parse_font.py
│  │  ├─ generate_hebrew_glyphs.py
│  │  ├─ rebuild_manifest.py
│  │  ├─ create_font.py
│  │  ├─ hebrew_mapping.py            (Hebrew letter ↔ char-code table)
│  │  └─ helpers/                     (diagnostics, optional)
│  ├─ text/                           <- Part 2
│  │  ├─ extract_text.py
│  │  ├─ build_translation.py
│  │  ├─ inject_translation.py
│  │  └─ translation_lookup.py        (shared 13-step cascade)
│  └─ reverse-engineering/            <- Part 3
│     └─ apply_reverse_patch.py
└─ translations/
   ├─ mapping.txt                     (main english === hebrew, Windows-1255)
   ├─ extra_mapping.txt               (UTF-8 fallback)
   └─ mi2/                            <- line-synced Hebrew for Monkey Island 2
      ├─ he.speech.txt
      └─ he.uitext.txt
```

---

## License & contributions

Issues and PRs welcome — particularly:

- new mapping entries for currently-untranslated strings,
- improved Hebrew translations for context-specific phrases,
- adapting Parts 1 / 3 to other LucasArts Special Editions
  (Monkey Island 1 SE, Indiana Jones, ...).

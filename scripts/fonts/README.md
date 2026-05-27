# Hebrew Font Builder

Tools for retrofitting Hebrew glyphs into the bitmap fonts used by LucasArts
Special Edition games (Monkey Island 2 SE and friends). The scripts unpack each
`.font` + `.png` atlas pair into individual glyph PNGs, render Hebrew letters
into the unused European-character slots, then repack everything back into a
new `.font` + atlas pair the game engine accepts.

The pipeline is **path-agnostic** — every script takes its source/target
directories as explicit arguments, so you can run them from any working
directory.

---

## At a glance

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1. parse_font.py             .font + atlas.png ─► glyph_dir/NNN.png +  │
│                                                    glyph_manifest.csv   │
│  2. generate_hebrew_glyphs.py glyph_dir/NNN.png  ─► Hebrew glyph PNGs   │
│                               (overwrites European-character slots)    │
│  3. rebuild_manifest.py       glyph_manifest.csv ─► updated metrics    │
│  4. create_font.py            glyph_dir         ─► new .font + atlas   │
└─────────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
              build_hebrew_font.py = orchestrator running all 4 steps
```

There is one orchestrator script (`build_hebrew_font.py`) that runs all four
steps for you. It accepts a fonts directory and (optionally) a single font
name, and processes either that one font or every `*.font` file in the folder.

---

## Prerequisites

### Tools you need

| Tool | Why |
|---|---|
| Python ≥ 3.9 | All scripts |
| `Pillow` (PIL) | Image manipulation |
| `numpy` | Pixel quantization in `generate_hebrew_glyphs.py` |
| A Hebrew TTF font | Source for rendering the new glyphs |
| A `.pak` extractor (e.g. QuickBMS) | Unpacking the game archive |

Install Python deps:

```sh
pip install Pillow numpy
```

### Files you need from the game

Extract the game's resource archive (e.g. `monkey2.pak`) with QuickBMS or any
other tool that supports the format. The output will contain a `fonts/`
directory with files like:

```
fonts/
  MinisterT_24.font
  MinisterT_24.png
  MinisterT_96.font
  MinisterT_96.png
  ... (one .font + .png pair per font/size variant)
```

Each `.font` file is a fixed-size 19 472-byte binary describing 155 glyphs;
the matching `.png` is the texture atlas.

### Hebrew TTF source font

The default scripts target Windows-installed Frank Ruehl fonts:

* Regular / italic variants → `C:\Windows\Fonts\frank.ttf`
* Bold / bold-oblique variants → `C:\Windows\Fonts\FRANKB.TTF`

Pass `--ttf PATH` / `--ttf-bold PATH` to override these with any other Hebrew
TTF (e.g. Open Sans Hebrew, David, Miriam, ...).

---

## **Back up your fonts before running any script**

The scripts overwrite the `.font` and `.png` files **in place** unless you
pass `--output-dir` to a separate directory. You are responsible for keeping
a clean copy of the original files. The simplest setup:

```sh
# After extracting the pak file
cp -r path/to/extracted/fonts  path/to/extracted/fonts.bak
```

(Or on Windows: `xcopy /E /I fonts fonts.bak`.)

The orchestrator also supports an explicit "originals" directory so you can
read the pristine `.font` templates from one place and write the rebuilt
files to another — see `--original-dir` / `--output-dir` below.

---

## Quick start

The simplest invocation: process every font in a directory with sensible
defaults.

```sh
python scripts/fonts/build_hebrew_font.py  "C:/extracted/fonts.bak"  \
       --output-dir "C:/extracted/fonts"
```

To process a single font only:

```sh
python scripts/fonts/build_hebrew_font.py  "C:/extracted/fonts.bak"  MinisterT_24  \
       --output-dir "C:/extracted/fonts"
```

When the script finishes, copy the updated `fonts/` directory back into the
game's archive (repack with the same tool you used to extract).

### Useful orchestrator flags

| Flag | Meaning |
|---|---|
| `--output-dir DIR` | Where to write the new `.font` + `.png` files. Default: overwrite in place. |
| `--original-dir DIR` | Folder with the untouched original `.font` templates (used to preserve the binary header / char-table verbatim). Defaults to `fonts_dir`. |
| `--ttf PATH` / `--ttf-bold PATH` | Override the Hebrew TTF source font(s). |
| `--max-fraction N` | Max Hebrew letter height as a fraction of the cell height (default `0.70`). |
| `--hebrew-gap N` | Extra pixels of advance after each Hebrew glyph (default `1`). |
| `--align left\|center` | Letter placement inside its glyph slot. `left` (default) puts pixels at column 0, matching the original atlas convention. |
| `--no-quantize` | Disable the default 5-level RGBA quantization that matches the game's color encoding. |
| `--clean` | Delete the intermediate `glyph_dir/` subfolder after each font is built. |
| `--dry-run` | Print what would happen without writing anything. |

`build_hebrew_font.py --help` prints the full list.

### Bold-variant auto-detection

The orchestrator picks the bold TTF (`--ttf-bold`) when the font name contains
`_b_` or `_bo_`. Everything else uses `--ttf`. Border-outline fonts (names
containing `_bo_`) automatically get a black inner-contour border painted on
each Hebrew glyph after quantization, so the game renders them with the same
outline treatment as the original Latin letters.

---

## Step-by-step reference

Each script can also be run standalone if you only need to redo one phase.

### 1. `parse_font.py` — Unpack a font into glyph PNGs + manifest

```sh
python scripts/fonts/parse_font.py  path/to/font.font
```

| Input | Output |
|---|---|
| `<name>.font` (19 472 bytes) | `<name>/NNN.png` (one PNG per glyph slot) |
| `<name>.png` (atlas image) | `<name>/glyph_manifest.csv` (all metrics) |

The companion `<name>.png` is read from the same directory as the `.font`
file (matching base name).

**Glyph manifest columns** (used by every later step):

| Column | Meaning |
|---|---|
| `glyph_index` | 1-based index inside the `.font` binary |
| `char_code` | Codepoint that maps to this glyph in the char-table |
| `row` | Row number in the atlas (1-based) |
| `x_left`, `x_right` | Left and right inclusive columns in the atlas |
| `y_top`, `y_bottom` | Top and bottom rows in the atlas |
| `width` | `x_right − x_left` (stored width in the binary) |
| `height` | Cell height — same for every glyph in a font |
| `bearing_x`, `advance_x` | Horizontal metrics from the `.font` record |
| `empty` | `1` if the slot is a placeholder, `0` otherwise |

The script never modifies the `.font` file — it only reads it.

### 2. `generate_hebrew_glyphs.py` — Render Hebrew letters into existing slots

```sh
python scripts/fonts/generate_hebrew_glyphs.py  <glyph_dir>  [--font TTF] [--max-fraction N] [--align MODE]
```

For every letter in `HEBREW_TO_CODE` (see `hebrew_mapping.py`), this script:

1. Picks a font size that makes the tallest test letter `ץ` fit within
   `max-fraction × cell_height` (binary search).
2. Computes a shared baseline across all Hebrew letters so ascenders /
   descenders behave consistently.
3. Renders each letter as an RGBA PNG with the original cell height.
4. Saves it as `<glyph_index>.png` in the glyph directory, **overwriting**
   the existing European-character glyph that lived in that slot.

The script reads `glyph_manifest.csv` to learn the cell height and slot
widths. It does **not** modify the manifest itself — step 3 does that.

Key flags:

* `--font PATH` / `--max-fraction N` — see above.
* `--align left` (default): every Hebrew glyph PNG is its natural width, with
  the letter starting at column 0 and exactly one transparent column on the
  right. This matches the original Latin glyph convention and lets
  `rebuild_manifest.py` adapt `advance_x` per letter.
* `--align center`: letter is centred inside the original slot width
  (PNG width unchanged → metrics stay fixed).
* `--no-quantize` — turn off the default snap to the 5 RGBA levels found in
  the original atlas.
* `--from N` / `--to N` — render only a subset of the 27 letters.

### 3. `rebuild_manifest.py` — Recompute glyph metrics for the new glyphs

```sh
python scripts/fonts/rebuild_manifest.py  <glyph_dir>  [--hebrew-gap N]
```

Updates `glyph_manifest.csv` so the Hebrew glyphs introduced in step 2 get
sensible `bearing_x`, `width`, `advance_x` values. Non-Hebrew rows are
**preserved** byte-for-byte (their `x_left` / `x_right` are not touched),
which prevents the game engine from mis-reading the atlas layout.

Key flags:

* `--hebrew-gap N` — extra pixels of horizontal advance added after each
  Hebrew letter (`advance_x = letter_width + hebrew_gap`, default `1`).

### 4. `create_font.py` — Repack glyphs + manifest into a new `.font` + atlas

```sh
python scripts/fonts/create_font.py  <glyph_dir>  <output_dir>  --template <original.font>
```

Reads every `NNN.png` and the manifest, lays the glyphs out in the atlas, and
writes the matching `.font` binary.

| Argument | Purpose |
|---|---|
| `glyph_dir` | Folder with `NNN.png` files and `glyph_manifest.csv`. |
| `output_dir` | Where to write the new `.font` + `.png` pair. |
| `--name NAME` | Base name for the output files. Defaults to the glyph_dir folder name. |
| `--template PATH` | **Strongly recommended.** Original `.font` file; its header and char-table are copied verbatim — only glyph metric records are rewritten. Without this, the header is reconstructed from known values which is less safe. |
| `--atlas-width N` | Force a specific atlas pixel width (power of two). Default: auto-detect from the template's companion `.png`. |
| `--repack` | Recompute a fresh left-to-right packing layout. Use only when glyph widths differ from the original — for a normal Hebrew rebuild, leave this OFF so non-Hebrew glyph positions remain byte-identical. |

When the four steps complete you get back the pair you started with, plus
the intermediate `glyph_dir/` if you didn't pass `--clean`:

```
output_dir/
  MinisterT_24.font     <- updated binary
  MinisterT_24.png      <- updated atlas with Hebrew glyphs
glyph_dir/
  MinisterT_24/
    001.png … 155.png
    glyph_manifest.csv
```

---

## Customizing the Hebrew mapping

`scripts/fonts/hebrew_mapping.py` defines which Hebrew letter goes into which
char-code slot:

```python
HEBREW_TO_CODE = {
    "א": 192,   # Alef
    "ג": 196,   # Gimel
    "ד": 197,   # Dalet
    ...
}
```

Char codes 192–254 are the "extended European" slots that exist in every
Monkey Island 2 SE font and hold accented Latin letters in the original game.
They are unused by an English/Hebrew translation, so they're safe to repurpose.

If you add a new letter:

1. Pick a free char code from the `HEBREW_TO_CODE` map (or one of the
   commented-out entries).
2. Add the entry to `HEBREW_TO_CODE`.
3. Add the corresponding glyph index to `_CODE_TO_GLYPH` (you'll see this in
   the manifest CSV after step 1).
4. Re-run the pipeline.

The same module is imported by `inject_translation.py`, so any change here
propagates to the translation pipeline automatically.

---

## End-to-end workflow

```sh
# 0. Extract the game archive (QuickBMS or equivalent)
quickbms scripts/quickbms.bms  game/monkey2.pak  extracted/

# 1. Back up the original fonts
xcopy /E /I extracted/fonts  extracted/fonts.bak

# 2. Build Hebrew-enabled fonts
python scripts/fonts/build_hebrew_font.py  extracted/fonts.bak \
    --output-dir extracted/fonts \
    --ttf      "C:/Windows/Fonts/frank.ttf" \
    --ttf-bold "C:/Windows/Fonts/FRANKB.TTF"

# 3. Re-pack the modified files back into the game archive
#    (use whichever repacker matches your extraction tool)
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ERROR: glyph_manifest.csv not found` | Step 1 (`parse_font.py`) hasn't been run for this glyph dir. |
| Black squares / wrong glyphs in game | Char-code → glyph-index map was disturbed. Re-run with `--template <original.font>` so the char-table is preserved. |
| Game hangs on startup | The atlas layout drifted. Re-run **without** `--repack` and pass `--align left` so non-Hebrew slots stay at their original positions. |
| Hebrew letters appear cut off | Increase `--max-fraction` (default `0.70`) to give them more vertical room, or change the TTF. |
| Letters touch each other | Increase `--hebrew-gap` (e.g. `--hebrew-gap 2`). |
| Hebrew text appears reversed in game | That's expected — text reversal is handled by the EXE patch (`scripts/reverse-engineering/apply_reverse_patch.py`), not by the font builder. |
| Pixels look "fuzzier" than the original Latin glyphs | Make sure pixel quantization is on (default). Check that you have not passed `--no-quantize`. |

---

## File layout

```
scripts/fonts/
  README.md                       <- you are here
  build_hebrew_font.py            <- orchestrator (runs all 4 steps)
  parse_font.py                   <- step 1: unpack .font → PNGs + manifest
  generate_hebrew_glyphs.py       <- step 2: render Hebrew letters
  rebuild_manifest.py             <- step 3: recompute Hebrew glyph metrics
  create_font.py                  <- step 4: pack PNGs → new .font + atlas
  hebrew_mapping.py               <- char-code → Hebrew letter table
  helpers/                        <- diagnostics, never required for a normal build
```

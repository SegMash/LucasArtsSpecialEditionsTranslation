"""
parse_font.py - Parse a Monkey Island 2 SE .font file and extract each glyph
                as an individual PNG image from the corresponding .png atlas.

Usage:
    python parse_font.py <path_to_font_file>

Example:
    python parse_font.py "c:/GOG Games/Monkey Island 2 SE/quickbms/output/fonts/MinisterT_24.font"

The script reads the binary .font file (always 19472 bytes) to get the exact
pixel bounding-box of every glyph in the PNG atlas, then saves each glyph as
a separate PNG.  This is accurate even for multi-part letters like Ü (letter
body + two dots are a single glyph cell).

.font file layout (confirmed via binary analysis):
  Offset    0: uint32  version = 5
  Offset    4: uint32  num_glyphs = 155
  Offset    9: uint8   first_printable = 33 ('!') — informational only;
                       the char table actually starts at codepoint 31
  Offset   90: uint16[155]  PRIMARY char-code-to-glyph-index table
                codepoints 31..185  ->  glyph index (1-based; 0 = no glyph)
                  cp31 = special game char (glyph 1, visible)
                  cp32 = SPACE            (glyph 2, transparent)
                  cp33 = '!'              (glyph 3)
  Offset  400: uint16[...]  EXTENDED char-code table (same format, sparse)
                starts where the primary table ends; entries for codes 186+
                (and Windows-1252 codes 128-159 are in the primary table)
  Offset 16992 (= 19472 - 155*16): glyph metric table, 16 bytes per glyph
    Each record for glyph G (1-based index):
      +00 uint16  x_left    : left pixel of glyph in the PNG atlas (inclusive)
      +02 uint16  layer     : always 1
      +04 uint16  x_right   : right pixel of glyph in the PNG atlas (INCLUSIVE —
                              the pixel at column x_right belongs to this glyph;
                              PIL crops must use x_right + 1 as the exclusive end)
      +06 uint16  y_bottom  : bottom of the row (exclusive), same for all glyphs
                              on the same row; y_top = y_bottom - cell_height
      +08 int16   bearing_x : horizontal pen offset before drawing
      +10 uint16  width     : glyph width in pixels (= x_right - x_left; does NOT
                              count the inclusive boundary pixel at x_right)
      +12 uint16  advance_x : pen advance after drawing
      +14 uint16  (zero)

  cell_height = y_bottom of the very first glyph record (same for all rows)

Output:
  A sub-folder named after the .font file (without extension) is created next
  to the input file.  Files are named:
    001.png .. 155.png  (by glyph index, 1-based)
  Plus a glyph_manifest.csv with columns:
    glyph_index, char_code, char, x_left, y_top, x_right, y_bottom,
    bearing_x, width, height, advance_x, row
"""

import sys
import os
import csv
import struct
import argparse
import numpy as np
from PIL import Image


# ── Constants ────────────────────────────────────────────────────────────────

FILE_SIZE        = 19472
NUM_GLYPHS       = 155
GLYPH_REC_OFFSET = FILE_SIZE - NUM_GLYPHS * 16   # = 16992
CHAR_TABLE_OFFSET = 90
CHAR_TABLE_FIRST  = 31   # cp31 (special game char); cp32=SPACE, cp33='!'
CHAR_TABLE_COUNT  = 155   # covers codes 31..185
CHAR_TABLE_END    = CHAR_TABLE_OFFSET + CHAR_TABLE_COUNT * 2   # = 400


# ── Helpers ──────────────────────────────────────────────────────────────────

def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]

def s16(data: bytes, off: int) -> int:
    return struct.unpack_from("<h", data, off)[0]


def read_char_tables(data: bytes) -> dict[int, int]:
    """
    Return a mapping  char_code (int) -> glyph_index (1-based).
    Reads both the primary table (codes 33-187) and the extended sparse table
    (everything after offset 400, up to the glyph metric block at 16992).
    Entries with value 0 are skipped (no glyph for that codepoint).
    """
    mapping: dict[int, int] = {}

    # Primary table: codes 33 .. 187
    for i in range(CHAR_TABLE_COUNT):
        gidx = u16(data, CHAR_TABLE_OFFSET + i * 2)
        if gidx > 0:
            mapping[CHAR_TABLE_FIRST + i] = gidx

    # Extended table: codes starting from 188
    # Same uint16 format, one entry per code, sparse (most are 0).
    # The table runs from CHAR_TABLE_END to GLYPH_REC_OFFSET.
    ext_start = CHAR_TABLE_END
    num_ext   = (GLYPH_REC_OFFSET - ext_start) // 2
    for i in range(num_ext):
        gidx = u16(data, ext_start + i * 2)
        if gidx > 0:
            code = CHAR_TABLE_FIRST + CHAR_TABLE_COUNT + i  # = 186 + i
            mapping[code] = gidx

    return mapping


def read_glyph_records(data: bytes) -> list[dict]:
    """
    Parse all 155 glyph metric records.
    Returns a list (0-indexed) of dicts.  glyph index 1 is at list position 0.
    """
    # cell_height = y_bottom of the first record (same for every row)
    cell_height = u16(data, GLYPH_REC_OFFSET + 6)

    records = []
    prev_y_bottom = 0
    row_num = 0
    for i in range(NUM_GLYPHS):
        off      = GLYPH_REC_OFFSET + i * 16
        x_left   = u16(data, off)
        layer    = u16(data, off + 2)
        x_right  = u16(data, off + 4)
        y_bottom = u16(data, off + 6)
        bearing_x = s16(data, off + 8)
        width    = u16(data, off + 10)
        advance_x = u16(data, off + 12)

        if y_bottom != prev_y_bottom:
            row_num += 1
            prev_y_bottom = y_bottom

        y_top = y_bottom - cell_height

        assert width == x_right - x_left, (
            f"Glyph {i+1}: width {width} != x_right {x_right} - x_left {x_left}"
        )

        records.append({
            "glyph_index": i + 1,
            "x_left":   x_left,
            "y_top":    y_top,
            "x_right":  x_right,
            "y_bottom": y_bottom,
            "bearing_x": bearing_x,
            "width":    width,
            "height":   cell_height,
            "advance_x": advance_x,
            "layer":    layer,
            "row":      row_num,
        })
    return records


# ── Main ─────────────────────────────────────────────────────────────────────

def is_glyph_empty(atlas_arr: np.ndarray, x_left: int, y_top: int,
                   x_right: int, y_bottom: int) -> bool:
    """Return True if the glyph's own pixel region has no visible (non-transparent) pixels.
    x_right is treated as inclusive (x_right+1 used for the slice end)."""
    region = atlas_arr[y_top:y_bottom, x_left:x_right + 1, 3]
    return region.size == 0 or region.max() == 0


def parse_font(font_path: str, tight_crop: bool = False,
               skip_empty: bool = False) -> None:
    if not os.path.isfile(font_path):
        print(f"ERROR: File not found: {font_path}")
        sys.exit(1)

    stem      = os.path.splitext(os.path.basename(font_path))[0]
    font_dir  = os.path.dirname(font_path)
    png_path  = os.path.join(font_dir, stem + ".png")
    out_dir   = os.path.join(font_dir, stem)

    if not os.path.isfile(png_path):
        print(f"ERROR: Companion PNG not found: {png_path}")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    # Read .font binary
    data = open(font_path, "rb").read()
    if len(data) != FILE_SIZE:
        print(f"WARNING: Expected {FILE_SIZE} bytes, got {len(data)}")

    version   = struct.unpack_from("<I", data, 0)[0]
    num_chars = struct.unpack_from("<I", data, 4)[0]
    print(f"Font: {stem}")
    print(f"  version={version}, num_glyphs={num_chars}")

    # Read char code -> glyph index mapping
    char_map = read_char_tables(data)
    # Build reverse: glyph_index -> char_code
    glyph_to_char: dict[int, int] = {}
    for code, gidx in char_map.items():
        if gidx not in glyph_to_char:   # keep first mapping if ambiguous
            glyph_to_char[gidx] = code

    # Read glyph metric records
    records = read_glyph_records(data)
    cell_height = records[0]["height"]
    print(f"  cell_height={cell_height}px  rows={records[-1]['row']}")

    # Load PNG atlas
    atlas = Image.open(png_path).convert("RGBA")
    atlas_arr = np.array(atlas)
    print(f"  Atlas size: {atlas.size[0]}x{atlas.size[1]}")

    # Save each glyph crop + write CSV
    csv_path = os.path.join(out_dir, "glyph_manifest.csv")
    saved = 0
    skipped_empty = []

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "glyph_index", "char_code", "char",
            "x_left", "y_top", "x_right", "y_bottom",
            "bearing_x", "width", "height", "advance_x", "row", "empty",
        ])

        for rec in records:
            gidx     = rec["glyph_index"]
            x_left   = rec["x_left"]
            y_top    = rec["y_top"]
            x_right  = rec["x_right"]
            y_bottom = rec["y_bottom"]

            char_code = glyph_to_char.get(gidx, 0)
            if char_code and 0 < char_code <= 255:
                try:
                    char_repr = bytes([char_code]).decode("cp1252")
                except (ValueError, UnicodeDecodeError):
                    char_repr = ""
            else:
                char_repr = ""

            # Detect whether the glyph's own region has any visible pixels
            empty = is_glyph_empty(atlas_arr, x_left, y_top, x_right, y_bottom)

            if empty:
                skipped_empty.append((gidx, char_code, char_repr))

            # Always write to CSV (so glyph_index numbering stays stable)
            writer.writerow([
                gidx, char_code, char_repr,
                x_left, y_top, x_right, y_bottom,
                rec["bearing_x"], rec["width"], rec["height"],
                rec["advance_x"], rec["row"],
                1 if empty else 0,
            ])

            # Skip saving the PNG file if empty and --skip-empty is set
            if empty and skip_empty:
                continue

            # Crop from atlas.
            # x_right is the last inclusive pixel column of this glyph, so PIL
            # (which uses an exclusive right boundary) needs x_right + 1.
            # No left padding: the cell's left margin is already the bearing gap
            # (transparent pixels between x_left and the actual ink).
            # Adding left padding would bleed visible pixels from the previous glyph.
            if tight_crop:
                crop = atlas.crop((x_left, y_top, x_right, y_bottom))
            else:
                crop = atlas.crop((x_left, y_top, x_right + 1, y_bottom))

            out_name = f"{gidx:03d}.png"
            crop.save(os.path.join(out_dir, out_name))
            saved += 1

    if skipped_empty:
        print(f"\n  Empty glyphs (transparent in atlas):")
        for gidx, code, ch in skipped_empty:
            ch_str = repr(ch) if ch else f"cp={code}"
            print(f"    Glyph {gidx:3d}  char={ch_str}  "
                  f"(placeholder slot with no pixels)")

    total = len(records)
    print(f"\nTotal glyph slots : {total}")
    print(f"Empty (skipped)   : {len(skipped_empty)}")
    print(f"Saved PNG images  : {saved}  ->  {out_dir}")
    print(f"CSV manifest      : {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract individual glyph images from a Monkey Island 2 SE .font + .png pair."
    )
    parser.add_argument("font_path", help="Path to the .font file.")
    parser.add_argument(
        "--tight", action="store_true",
        help="Crop strictly to [x_left, x_right) — omits the inclusive boundary "
             "pixel at x_right (default: include it via x_right+1).",
    )
    parser.add_argument(
        "--skip-empty", action="store_true",
        help="Do not write a PNG file for empty (fully transparent) glyphs.",
    )
    args = parser.parse_args()
    parse_font(args.font_path, tight_crop=args.tight, skip_empty=args.skip_empty)

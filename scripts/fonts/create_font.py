"""
create_font.py - Pack individual glyph PNGs back into a .font + .png atlas pair.

This is the inverse of parse_font.py.

Usage:
    python create_font.py <glyph_dir> <output_dir> [--name NAME] [--template FONT_FILE]

Arguments:
    glyph_dir      Folder containing the numbered glyph PNGs (001.png … 155.png)
                   and a glyph_manifest.csv (as produced by parse_font.py).
    output_dir     Destination folder for the generated .font and .png files.
    --name NAME    Base name for the output files.
                   Default: same as the glyph_dir folder name (e.g. "MinisterT_24").
    --template     Path to the ORIGINAL .font file.  When supplied, its header and
                   char-table bytes are copied verbatim (only the glyph metric
                   records are updated).  Strongly recommended — it preserves any
                   bytes whose meaning was not fully reverse-engineered.
                   If omitted, the header is reconstructed from known values.

Workflow
--------
1. Reads glyph_manifest.csv for glyph ordering, row grouping, bearing_x,
   advance_x and cell_height.
2. Loads each NNN.png glyph image.  Missing files (e.g. from --skip-empty)
   are replaced with a transparent placeholder.
3. Recomputes the atlas layout: glyphs are packed left-to-right within each
   row in glyph-index order, with a small gap between them.
   * The glyph's stored width  = png_width - 1
     (matching parse_font's crop convention: crop ends at x_right + 1)
   * advance_x and bearing_x are taken from the manifest — edit those columns
     in the CSV before running if you need to adjust letter spacing.
4. Writes the atlas PNG (dimensions rounded up to the next power of 2).
5. Writes the .font binary with updated glyph metric records.

.font glyph record layout (16 bytes, little-endian):
    +00 uint16  x_left    left pixel column in atlas (inclusive)
    +02 uint16  layer     always 1
    +04 uint16  x_right   = x_left + (png_width - 1)  [last inclusive column]
    +06 uint16  y_bottom  bottom of this row (exclusive); y_top = y_bottom - cell_height
    +08 int16   bearing_x taken from manifest
    +10 uint16  width     = x_right - x_left = png_width - 1
    +12 uint16  advance_x taken from manifest
    +14 uint16  0
"""

import sys
import os
import csv
import struct
import argparse
from PIL import Image

# ── Constants (must match parse_font.py) ─────────────────────────────────────
FILE_SIZE         = 19472
NUM_GLYPHS_FIXED  = 155
GLYPH_REC_OFFSET  = FILE_SIZE - NUM_GLYPHS_FIXED * 16   # 16992
CHAR_TABLE_OFFSET = 90
CHAR_TABLE_FIRST  = 31          # first codepoint in the primary char table
CHAR_TABLE_COUNT  = 155         # covers codes 31..185
CHAR_TABLE_END    = CHAR_TABLE_OFFSET + CHAR_TABLE_COUNT * 2   # 400

# Layout parameters
# GLYPH_GAP=0: tightly packed — the x_right+1 crop convention already keeps
# glyphs non-overlapping, so 0 extra gap is correct for a round-trip rebuild.
# Increase only if you want visible breathing room between glyphs in the atlas.
GLYPH_GAP  = 0
ROW_MARGIN = 1   # base x before each row; actual x_left = ROW_MARGIN + max(bearing_x, 0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def next_pow2(n: int) -> int:
    """Return the smallest power of 2 that is >= n."""
    p = 1
    while p < n:
        p <<= 1
    return p


def fit_to_cell(img: Image.Image, cell_height: int) -> Image.Image:
    """
    Return a new RGBA image that is exactly cell_height pixels tall:
      - If img.height == cell_height: returned unchanged.
      - If img.height < cell_height: vertically centred on a transparent canvas.
      - If img.height > cell_height: cropped from the top.
    Width is always preserved.
    """
    if img.height == cell_height:
        return img
    canvas = Image.new("RGBA", (img.width, cell_height), (0, 0, 0, 0))
    if img.height < cell_height:
        y_off = (cell_height - img.height) // 2
    else:
        # Crop: keep the top cell_height rows
        img    = img.crop((0, 0, img.width, cell_height))
        y_off  = 0
    canvas.paste(img, (0, y_off), img)
    return canvas


# ── Main ──────────────────────────────────────────────────────────────────────

def pack_font(glyph_dir: str, output_dir: str, name: str,
              template_path: str | None,
              atlas_width_hint: int | None = None,
              repack: bool = False) -> None:

    # ── 1. Read manifest ──────────────────────────────────────────────────────
    csv_path = os.path.join(glyph_dir, "glyph_manifest.csv")
    if not os.path.isfile(csv_path):
        print(f"ERROR: glyph_manifest.csv not found in: {glyph_dir}")
        sys.exit(1)

    glyphs: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            glyphs.append({
                "glyph_index": int(row["glyph_index"]),
                "char_code":   int(row["char_code"]),
                "bearing_x":   int(row["bearing_x"]),
                "advance_x":   int(row["advance_x"]),
                "height":      int(row["height"]),   # cell_height (same for all)
                "row":         int(row["row"]),
                "empty":       row.get("empty", "0") == "1",
                # Original atlas coordinates — used for placement in default mode
                "orig_x_left":   int(row["x_left"]),
                "orig_x_right":  int(row["x_right"]),
                "orig_y_top":    int(row["y_top"]),
                "orig_y_bottom": int(row["y_bottom"]),
                "orig_width":    int(row["width"]),
            })

    if not glyphs:
        print("ERROR: glyph_manifest.csv contains no data rows.")
        sys.exit(1)

    cell_height = glyphs[0]["height"]
    num_rows    = max(g["row"] for g in glyphs)
    num_glyphs  = len(glyphs)

    # Auto-detect atlas width from the companion PNG of the template font
    if atlas_width_hint is None and template_path:
        stem        = os.path.splitext(template_path)[0]
        orig_png    = stem + ".png"
        if os.path.isfile(orig_png):
            from PIL import Image as _Img
            with _Img.open(orig_png) as _im:
                atlas_width_hint = _im.width

    print(f"Font: {name}")
    print(f"  Glyphs: {num_glyphs}  cell_height: {cell_height}px  rows: {num_rows}")

    # ── 2. Load glyph PNGs ────────────────────────────────────────────────────
    missing = []
    for g in glyphs:
        idx  = g["glyph_index"]
        path = os.path.join(glyph_dir, f"{idx:03d}.png")
        if os.path.isfile(path):
            img = Image.open(path).convert("RGBA")
        else:
            # Not present (e.g. empty glyph saved with --skip-empty):
            # create a 2 × cell_height transparent placeholder so the glyph
            # still occupies a slot in the atlas.
            img = Image.new("RGBA", (2, cell_height), (0, 0, 0, 0))
            missing.append(idx)
        g["img"] = fit_to_cell(img, cell_height)

    if missing:
        print(f"  Missing PNG files (transparent placeholders used): {missing}")

    # ── 3. Build atlas ────────────────────────────────────────────────────────
    # Group glyphs by row, preserving glyph_index order within each row.
    rows_map: dict[int, list[dict]] = {}
    for g in glyphs:
        rows_map.setdefault(g["row"], []).append(g)

    content_h = num_rows * cell_height

    if not repack:
        # ── Preserve-original mode ──────────────────────────────────────────
        # Place each glyph at the coordinates already recorded in the manifest.
        # This guarantees byte-identical glyph metric records when the glyph
        # images haven't changed (perfect round-trip).

        # Determine atlas size from the manifest coordinates
        max_x = max(g["orig_x_right"] for g in glyphs) + 1
        if atlas_width_hint:
            atlas_w = atlas_width_hint
        else:
            atlas_w = next_pow2(max(max_x, 64))
        atlas_h = next_pow2(max(content_h, 64))

        print(f"  Atlas: {atlas_w}×{atlas_h}px  (preserve-original mode)")

        atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

        size_warnings = 0
        for g in glyphs:
            img          = g["img"]
            x_left       = g["orig_x_left"]
            x_right      = g["orig_x_right"]
            y_top        = g["orig_y_top"]
            y_bottom     = g["orig_y_bottom"]
            orig_w       = g["orig_width"]    # stored width = x_right - x_left

            png_w = img.width
            # Stored width from the PNG: png_w - 1  (parse_font saves width+1 pixels)
            new_stored_w = max(png_w - 1, 0)

            if new_stored_w != orig_w:
                # Glyph was resized — update metrics to match the new image.
                # x_left stays the same; x_right and width adjust.
                x_right  = x_left + new_stored_w
                stored_w = new_stored_w
                size_warnings += 1
                if size_warnings <= 10:
                    print(f"  INFO: glyph {g['glyph_index']:03d} resized "
                          f"({orig_w+1}→{new_stored_w+1}px wide); "
                          f"x_right updated {g['orig_x_right']}→{x_right}")
            else:
                stored_w = orig_w

            if x_right >= atlas_w:
                print(f"  WARNING: glyph {g['glyph_index']:03d} right edge {x_right} "
                      f">= atlas width {atlas_w}. Atlas may need to be wider.")

            atlas.paste(img, (x_left, y_top), img)

            g["new_x_left"]   = x_left
            g["new_x_right"]  = x_right
            g["new_y_top"]    = y_top
            g["new_y_bottom"] = y_bottom
            g["new_width"]    = stored_w

        if size_warnings > 10:
            print(f"  INFO: ... and {size_warnings - 10} more resized glyphs (use --repack for full repacking).")

    else:
        # ── Repack mode ─────────────────────────────────────────────────────
        # Recompute a fresh left-to-right packing layout.  Needed when glyphs
        # are replaced with images of different widths (e.g. Hebrew letters).

        # First pass: determine required content width.
        # Use the same bearing_x gap rule as the packing loop below.
        content_w = 0
        for row_glyphs in rows_map.values():
            row_w = ROW_MARGIN
            for g in row_glyphs:
                row_w += max(g["bearing_x"], 0) + g["img"].width
            content_w = max(content_w, row_w)

        if atlas_width_hint and atlas_width_hint >= content_w:
            atlas_w = atlas_width_hint
        else:
            atlas_w = next_pow2(max(content_w, 64))
            if atlas_width_hint and atlas_width_hint < content_w:
                print(f"  WARNING: requested atlas width {atlas_width_hint}px is too narrow "
                      f"for content ({content_w}px). Using {atlas_w}px instead.")

        atlas_h = next_pow2(max(content_h, 64))
        print(f"  Atlas: {atlas_w}×{atlas_h}px  (repack mode, content: {content_w}×{content_h}px)")

        atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

        for row_num in sorted(rows_map):
            y_top    = (row_num - 1) * cell_height
            y_bottom = row_num * cell_height
            current_x = ROW_MARGIN

            for g in rows_map[row_num]:
                img      = g["img"]
                png_w    = img.width
                stored_w = max(png_w - 1, 0)

                # The original atlas packing rule: x_left = current_x + max(bearing_x, 0)
                # for every glyph including the first in the row.  With ROW_MARGIN=1 this
                # reproduces the original positions exactly for non-Hebrew glyphs.
                gap     = max(g["bearing_x"], 0)
                x_left  = current_x + gap
                x_right = x_left + stored_w

                if x_right >= atlas_w:
                    print(f"  WARNING: glyph {g['glyph_index']:03d} right edge {x_right} "
                          f">= atlas width {atlas_w}.  Atlas may need to be wider.")

                atlas.paste(img, (x_left, y_top), img)

                g["new_x_left"]   = x_left
                g["new_x_right"]  = x_right
                g["new_y_top"]    = y_top
                g["new_y_bottom"] = y_bottom
                g["new_width"]    = stored_w

                current_x = x_right + 1

    # ── 4. Save atlas PNG ─────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    atlas_out = os.path.join(output_dir, f"{name}.png")
    atlas.save(atlas_out, compress_level=9)
    print(f"  Saved atlas  : {atlas_out}")

    # ── 5. Build .font binary ─────────────────────────────────────────────────
    if template_path and os.path.isfile(template_path):
        raw = open(template_path, "rb").read()
        if len(raw) == FILE_SIZE:
            font_data = bytearray(raw)
            print(f"  Template     : {template_path}  (header/char-table preserved)")
        else:
            print(f"  WARNING: template size {len(raw)} != {FILE_SIZE}; "
                  f"reconstructing header from scratch.")
            font_data = None
    else:
        font_data = None

    if font_data is None:
        font_data = bytearray(FILE_SIZE)
        # Reconstruct header from known values
        struct.pack_into("<I", font_data, 0, 5)            # version = 5
        struct.pack_into("<I", font_data, 4, num_glyphs)   # num_glyphs
        # Bytes 8-11: observed constant in all analyzed fonts
        font_data[8] = 0x22
        font_data[9] = 0x21
        # Bytes 12-15 and 24-27: observed constant 16864 (0x41E0)
        struct.pack_into("<I", font_data, 12, 16864)
        struct.pack_into("<I", font_data, 24, 16864)

        # Build primary char table (codes 31..185)
        char_to_glyph = {g["char_code"]: g["glyph_index"] for g in glyphs}
        for i in range(CHAR_TABLE_COUNT):
            code = CHAR_TABLE_FIRST + i
            gidx = char_to_glyph.get(code, 0)
            struct.pack_into("<H", font_data,
                             CHAR_TABLE_OFFSET + i * 2, gidx)

        # Build extended char table (codes 186+)
        num_ext = (GLYPH_REC_OFFSET - CHAR_TABLE_END) // 2
        for i in range(num_ext):
            code = CHAR_TABLE_FIRST + CHAR_TABLE_COUNT + i   # 186 + i
            gidx = char_to_glyph.get(code, 0)
            struct.pack_into("<H", font_data,
                             CHAR_TABLE_END + i * 2, gidx)

        print("  Header reconstructed from scratch (no template provided).")

    # Update glyph metric records at GLYPH_REC_OFFSET.
    # When a template is used, we overwrite x_left, x_right, width, bearing_x,
    # and advance_x (all taken from the manifest).  The layer byte (+2),
    # y_bottom (+6), and the trailing zero (+14) are preserved verbatim from
    # the template so that any undocumented fields remain intact.
    have_template = (template_path and os.path.isfile(template_path)
                     and len(open(template_path, "rb").read()) == FILE_SIZE)

    for g in glyphs:
        idx = g["glyph_index"]
        off = GLYPH_REC_OFFSET + (idx - 1) * 16

        if have_template:
            # Update position and spacing fields; leave layer/y_bottom/unknown
            # bytes from the template verbatim.
            struct.pack_into("<H", font_data, off,      g["new_x_left"])   # x_left     +0
            struct.pack_into("<H", font_data, off + 4,  g["new_x_right"])  # x_right    +4
            struct.pack_into("<h", font_data, off + 8,  g["bearing_x"])    # bearing_x  +8  (signed)
            struct.pack_into("<H", font_data, off + 10, g["new_width"])    # width      +10
            struct.pack_into("<H", font_data, off + 12, g["advance_x"])    # advance_x  +12
        else:
            # No template — reconstruct the full record.
            struct.pack_into("<H", font_data, off,      g["new_x_left"])
            struct.pack_into("<H", font_data, off + 2,  1)                 # layer
            struct.pack_into("<H", font_data, off + 4,  g["new_x_right"])
            struct.pack_into("<H", font_data, off + 6,  g["new_y_bottom"])
            struct.pack_into("<h", font_data, off + 8,  g["bearing_x"])    # signed
            struct.pack_into("<H", font_data, off + 10, g["new_width"])
            struct.pack_into("<H", font_data, off + 12, g["advance_x"])
            struct.pack_into("<H", font_data, off + 14, 0)

    font_out = os.path.join(output_dir, f"{name}.font")
    with open(font_out, "wb") as fh:
        fh.write(font_data)
    print(f"  Saved .font  : {font_out}  ({len(font_data)} bytes)")
    print("Done.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Pack individual glyph PNGs back into a Monkey Island 2 SE "
            ".font + .png atlas pair  (inverse of parse_font.py)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "glyph_dir",
        help="Folder with NNN.png glyph files and glyph_manifest.csv.",
    )
    parser.add_argument(
        "output_dir",
        help="Destination folder for the output .font and .png files.",
    )
    parser.add_argument(
        "--name", default=None,
        help="Base name for output files (default: glyph_dir folder name).",
    )
    parser.add_argument(
        "--template", default=None, metavar="FONT_FILE",
        help=(
            "Original .font file used as a binary template.  Its header and "
            "char-table bytes are copied verbatim; only the glyph metric "
            "records are updated.  Strongly recommended.  The companion .png "
            "file (same name, .png extension) is also used to auto-detect the "
            "target atlas width."
        ),
    )
    parser.add_argument(
        "--atlas-width", default=None, type=int, metavar="N",
        help=(
            "Force the output atlas to be exactly N pixels wide (must be a "
            "power of 2, e.g. 512 or 1024).  If omitted, the width is read "
            "from the original template PNG, or computed automatically."
        ),
    )
    parser.add_argument(
        "--repack", action="store_true",
        help=(
            "Recompute a fresh left-to-right packing layout instead of "
            "preserving the original glyph positions.  Use this when replacing "
            "glyphs with images of different widths (e.g. Hebrew letters)."
        ),
    )
    args = parser.parse_args()

    stem = args.name or os.path.basename(os.path.abspath(args.glyph_dir))
    pack_font(args.glyph_dir, args.output_dir, stem, args.template,
              atlas_width_hint=args.atlas_width, repack=args.repack)

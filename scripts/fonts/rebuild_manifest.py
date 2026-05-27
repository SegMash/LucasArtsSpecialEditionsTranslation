"""
rebuild_manifest.py — Update glyph_manifest.csv after generating Hebrew glyphs.

Run this after generate_hebrew_glyphs.py has overwritten the Hebrew letter
PNGs inside the glyph folder.  The script reads every NNN.png in the folder,
measures its actual pixel width, and rewrites glyph_manifest.csv.

Two modes
---------
Default (--preserve-positions, recommended):
    Only updates bearing_x and advance_x for Hebrew glyphs whose PNG was
    regenerated.  All x_left / x_right coordinates remain unchanged from the
    original parsed manifest.  This keeps non-Hebrew glyph positions identical
    to the original font, preventing the game engine from misinterpreting the
    atlas layout.  Use create_font.py WITHOUT --repack after this step.

Legacy (full repack, use --repack):
    Recomputes x_left / x_right for every glyph, starting from scratch per
    row.  Shifts non-Hebrew glyphs that follow Hebrew ones in the same row.
    This can confuse the game engine when glyphs near the end of the atlas
    change position (especially the last glyph in the last row).  Only use
    this mode if you need the atlas to be tightly packed with no gaps.

Usage
-----
    python rebuild_manifest.py <glyph_dir> [options]

    glyph_dir    Folder containing glyph_manifest.csv and NNN.png files.

Options
-------
    --repack        Use legacy full-repack mode (see above).
    --row-start N   x_left margin at the start of each row (repack mode only,
                    default: 1).
    --hebrew-gap N  Extra advance pixels after each Hebrew glyph (default: 1).

Typical workflow (recommended)
-------------------------------
    1. python parse_font.py MinisterT_24.font
       → creates MinisterT_24/ with original PNGs + glyph_manifest.csv

    2. python generate_hebrew_glyphs.py MinisterT_24/
       → overwrites NNN.png for the 27 Hebrew letter slots

    3. python rebuild_manifest.py MinisterT_24/
       → updates bearing_x / advance_x for changed Hebrew glyphs only;
         all x_left / x_right positions stay at original values

    4. python create_font.py MinisterT_24/ output/ --template MinisterT_24.font
       → packs everything (no --repack needed; x_right of Hebrew glyphs is
         auto-adjusted by create_font when the PNG width changed)
"""

import sys
import os
import csv
import argparse
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hebrew_mapping import HEBREW_TO_GLYPH


def png_width(path: str, fallback: int) -> int:
    """Return the pixel width of a PNG, or *fallback* if the file is missing."""
    if os.path.isfile(path):
        with Image.open(path) as img:
            return img.width
    return fallback


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update glyph_manifest.csv after generating Hebrew glyphs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "glyph_dir",
        help="Folder containing glyph_manifest.csv and NNN.png glyph files.",
    )
    parser.add_argument(
        "--repack", action="store_true",
        help="Full repack mode: recompute x_left / x_right for every glyph "
             "(legacy mode; can shift non-Hebrew glyphs and confuse the game engine).",
    )
    parser.add_argument(
        "--row-start", type=int, default=1, metavar="N",
        help="Base x margin at the start of each row (repack mode only, default: 1).",
    )
    parser.add_argument(
        "--hebrew-gap", type=int, default=1, metavar="N",
        help="Extra pixels of advance after each Hebrew glyph (default: 1). "
             "advance_x = letter_width + hebrew_gap.  Increase for more space "
             "between letters; decrease for tighter packing.",
    )
    args = parser.parse_args()

    glyph_dir = args.glyph_dir
    csv_path  = os.path.join(glyph_dir, "glyph_manifest.csv")

    if not os.path.isfile(csv_path):
        print(f"ERROR: glyph_manifest.csv not found in: {glyph_dir}")
        sys.exit(1)

    hebrew_indices = set(HEBREW_TO_GLYPH.values())

    # Per-glyph bearing_x overrides for Hebrew slots.
    # Add entries here when a specific glyph needs a non-standard bearing.
    # All other Hebrew glyphs default to bearing_x = 1.
    HEBREW_BEARING_OVERRIDES: dict[int, int] = {
        137: -1,
    }

    # ── 1. Read existing manifest ─────────────────────────────────────────────
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        source_rows = list(csv.DictReader(f))

    if not source_rows:
        print("ERROR: manifest is empty.")
        sys.exit(1)

    cell_h = int(source_rows[0]["height"])
    mode_str = "FULL REPACK (legacy)" if args.repack else "PRESERVE POSITIONS (recommended)"
    print(f"Glyph dir   : {os.path.abspath(glyph_dir)}")
    print(f"Cell height : {cell_h} px")
    print(f"Mode        : {mode_str}")
    if args.repack:
        print(f"Row start x : {args.row_start}   Hebrew gap : {args.hebrew_gap}")
    print()

    # ── 2. Group by row, preserving glyph-index order ────────────────────────
    rows_map: dict[int, list[dict]] = {}
    for row in source_rows:
        rows_map.setdefault(int(row["row"]), []).append(row)

    # ── 3. Compute updated fields per glyph ───────────────────────────────────
    updated: dict[int, dict] = {}

    if args.repack:
        # ── Legacy full-repack mode ───────────────────────────────────────────
        # Recomputes x_left / x_right for every glyph from scratch per row.
        # WARNING: shifts non-Hebrew glyphs that follow Hebrew ones in the same
        # row, which can cause the game engine to misinterpret the atlas layout.
        for row_num in sorted(rows_map):
            glyphs_in_row = rows_map[row_num]
            y_top    = (row_num - 1) * cell_h
            y_bottom = row_num       * cell_h
            current_x = args.row_start

            for g in glyphs_in_row:
                gidx   = int(g["glyph_index"])
                is_heb = gidx in hebrew_indices

                w_png    = png_width(os.path.join(glyph_dir, f"{gidx:03d}.png"),
                                     fallback=int(g["width"]) + 1)
                stored_w = max(w_png - 1, 0)
                png_changed = is_heb and (stored_w != int(g["width"]))

                if png_changed:
                    bearing_x = 1
                    advance_x = stored_w + args.hebrew_gap
                elif is_heb:
                    bearing_x = 1
                    advance_x = stored_w + args.hebrew_gap
                else:
                    bearing_x = int(g["bearing_x"])
                    advance_x = int(g["advance_x"])

                # Apply per-glyph bearing_x overrides (any glyph, Hebrew or not).
                if gidx in HEBREW_BEARING_OVERRIDES:
                    bearing_x = HEBREW_BEARING_OVERRIDES[gidx]

                gap     = max(bearing_x, 0)
                x_left  = current_x + gap
                x_right = x_left + stored_w
                empty   = 0 if is_heb else int(g["empty"])

                updated[gidx] = {
                    "x_left":       x_left,
                    "y_top":        y_top,
                    "x_right":      x_right,
                    "y_bottom":     y_bottom,
                    "bearing_x":    bearing_x,
                    "width":        stored_w,
                    "advance_x":    advance_x,
                    "empty":        empty,
                    "_png_changed": png_changed,
                }
                current_x = x_right + 1

            last_gidx = int(glyphs_in_row[-1]["glyph_index"])
            print(f"  Row {row_num}: {len(glyphs_in_row):>3} glyphs  "
                  f"x_right_last = {updated[last_gidx]['x_right']}")

    else:
        # ── Preserve-positions mode (default, recommended) ────────────────────
        # For every glyph the x_left stays at its original value so that
        # non-Hebrew glyph positions are byte-identical to the original font.
        #
        # For Hebrew glyphs whose PNG was regenerated:
        #   • x_right and width are updated to match the new PNG size
        #     (x_right = original_x_left + new_stored_w)
        #   • bearing_x and advance_x are updated for the Hebrew letter
        #
        # For all other glyphs (and unchanged Hebrew slots):
        #   • every field is kept exactly as in the existing manifest
        for row_num in sorted(rows_map):
            glyphs_in_row = rows_map[row_num]
            y_top    = (row_num - 1) * cell_h
            y_bottom = row_num       * cell_h

            for g in glyphs_in_row:
                gidx   = int(g["glyph_index"])
                is_heb = gidx in hebrew_indices

                w_png    = png_width(os.path.join(glyph_dir, f"{gidx:03d}.png"),
                                     fallback=int(g["width"]) + 1)
                stored_w = max(w_png - 1, 0)
                png_changed = is_heb and (stored_w != int(g["width"]))

                if png_changed:
                    bearing_x = 1
                    advance_x = stored_w + args.hebrew_gap
                    x_left  = int(g["x_left"])
                    x_right = x_left + stored_w
                    width   = stored_w
                elif is_heb:
                    bearing_x = 1
                    advance_x = stored_w + args.hebrew_gap
                    x_left  = int(g["x_left"])
                    x_right = int(g["x_right"])
                    width   = int(g["width"])
                else:
                    bearing_x = int(g["bearing_x"])
                    advance_x = int(g["advance_x"])
                    x_left  = int(g["x_left"])
                    x_right = int(g["x_right"])
                    width   = int(g["width"])

                # Apply per-glyph bearing_x overrides (any glyph, Hebrew or not).
                if gidx in HEBREW_BEARING_OVERRIDES:
                    bearing_x = HEBREW_BEARING_OVERRIDES[gidx]

                updated[gidx] = {
                    "x_left":       x_left,
                    "y_top":        int(g["y_top"]),
                    "x_right":      x_right,
                    "y_bottom":     int(g["y_bottom"]),
                    "bearing_x":    bearing_x,
                    "width":        width,
                    "advance_x":    advance_x,
                    "empty":        0 if is_heb else int(g["empty"]),
                    "_png_changed": png_changed,   # internal flag, not written to CSV
                }

            last_gidx = int(glyphs_in_row[-1]["glyph_index"])
            last_xr   = updated[last_gidx]["x_right"]
            print(f"  Row {row_num}: {len(glyphs_in_row):>3} glyphs  "
                  f"x_right_last = {last_xr}  (x_left/non-Hebrew positions preserved)")

    # ── 3b. Overlap fix (preserve-positions mode only) ────────────────────────
    # When a Hebrew glyph is wider than the original Latin glyph it replaced,
    # its x_right may reach into the next glyph's x_left, causing the two
    # images to overlap in the atlas.  Fix this by pushing any glyph whose
    # x_left falls inside the previous glyph's x_right rightward by exactly
    # enough to leave a 1-pixel gap, then cascade that shift through the rest
    # of the row so no subsequent glyph is displaced either.
    overlap_count = 0
    for row_num in sorted(rows_map):
        glyphs_in_row = rows_map[row_num]
        shift = 0   # cumulative rightward shift applied to this row so far
        for i in range(len(glyphs_in_row) - 1):
            gidx_a = int(glyphs_in_row[i]["glyph_index"])
            gidx_b = int(glyphs_in_row[i + 1]["glyph_index"])
            u_a    = updated[gidx_a]
            u_b    = updated[gidx_b]

            # Apply any accumulated shift to glyph B before checking
            u_b["x_left"]  += shift
            u_b["x_right"] += shift

            xr_a = u_a["x_right"]
            xl_b = u_b["x_left"]

            if xr_a >= xl_b:          # overlap: B starts inside or at A's right edge
                needed = xr_a - xl_b + 1   # how many pixels to push B right
                overlap_count += 1
                print(f"  OVERLAP FIX: glyph {gidx_a} x_right={xr_a} >= "
                      f"glyph {gidx_b} x_left={xl_b}  "
                      f"→ pushing glyph {gidx_b} (and rest of row) right by {needed}px")
                u_b["x_left"]  += needed
                u_b["x_right"] += needed
                shift += needed

    if overlap_count == 0:
        print("  No atlas overlaps detected.")

    # ── 4. Overwrite glyph_manifest.csv ──────────────────────────────────────
    fieldnames = [
        "glyph_index", "char_code", "char",
        "x_left", "y_top", "x_right", "y_bottom",
        "bearing_x", "width", "height", "advance_x", "row", "empty",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for row in source_rows:
            gidx = int(row["glyph_index"])
            u    = updated[gidx]
            writer.writerow([
                gidx,
                row["char_code"],
                row["char"],
                u["x_left"],
                u["y_top"],
                u["x_right"],
                u["y_bottom"],
                u["bearing_x"],
                u["width"],
                row["height"],
                u["advance_x"],
                row["row"],
                u["empty"],
            ])

    print()
    print(f"Updated glyph_manifest.csv  ({len(source_rows)} rows)  →  {csv_path}")
    # "changed" = Hebrew glyph whose PNG width differed from the original manifest
    changed = [gidx for gidx in sorted(hebrew_indices)
               if gidx in updated and updated[gidx].get("_png_changed", False)]
    print()
    if changed:
        print(f"Updated Hebrew glyphs ({len(changed)}) — PNG width changed, metrics recalculated:")
        print(f"  {'Glyph':>6}  {'x_left':>7}  {'x_right':>8}  {'width':>6}  {'bearing_x':>10}  {'advance_x':>10}")
        for gidx in changed:
            u = updated[gidx]
            print(f"  {gidx:>6}  {u['x_left']:>7}  {u['x_right']:>8}  "
                  f"{u['width']:>6}  {u['bearing_x']:>10}  {u['advance_x']:>10}")
    else:
        print("No Hebrew glyphs were regenerated — manifest is a no-op (all positions preserved).")

    print()
    if args.repack:
        print("Next step (repack mode):")
        print(f'  python create_font.py "{glyph_dir}" <output_dir> '
              f'--template <original.font> --repack')
    else:
        print("Next step (preserve-positions mode):")
        print(f'  python create_font.py "{glyph_dir}" <output_dir> '
              f'--template <original.font>')
        print("  (No --repack needed: x_right of changed Hebrew glyphs is "
              "auto-adjusted by create_font)")


if __name__ == "__main__":
    main()

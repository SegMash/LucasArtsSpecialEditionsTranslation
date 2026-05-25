"""
Audit: verify that the .font binary glyph records match actual pixel content
in the atlas PNG. Checks glyphs in the given index range.

Usage:
  python audit_atlas.py <font_file> <png_file> [from_glyph] [to_glyph]

Defaults to checking glyphs 122-155 (Hebrew range + end).
"""
import struct
import sys
import os
from PIL import Image

def u16(d, o): return struct.unpack_from("<H", d, o)[0]
def s16(d, o): return struct.unpack_from("<h", d, o)[0]

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        # Fall back to prompting for paths
        font_path = input("Font file path: ").strip()
        png_path  = input("PNG  file path: ").strip()
    else:
        font_path = args[0]
        png_path  = args[1]

    CHECK_FROM = int(args[2]) if len(args) > 2 else 122
    CHECK_TO   = int(args[3]) if len(args) > 3 else 155

    if not os.path.isfile(font_path):
        print(f"ERROR: font file not found: {font_path}")
        sys.exit(1)
    if not os.path.isfile(png_path):
        print(f"ERROR: PNG file not found: {png_path}")
        sys.exit(1)

    with open(font_path, "rb") as f:
        data = bytearray(f.read())

    FILE_SIZE = len(data)
    NUM_GLYPHS = 155
    GLYPH_REC_OFFSET = FILE_SIZE - NUM_GLYPHS * 16

    img = Image.open(png_path).convert("RGBA")
    px = img.load()

    # Auto-detect cell height from first glyph record
    CELL_HEIGHT = u16(data, GLYPH_REC_OFFSET + 6)

    print(f"Font: {os.path.basename(font_path)}")
    print(f"Atlas: {img.width}x{img.height}  cell_height={CELL_HEIGHT}")
    print(f"File size: {FILE_SIZE} ({hex(FILE_SIZE)}), GLYPH_REC_OFFSET: {GLYPH_REC_OFFSET} ({hex(GLYPH_REC_OFFSET)})")
    print(f"Checking glyphs {CHECK_FROM} – {CHECK_TO}")
    print()
    print("idx  | xl   xr   bx   w    ax  | has_pixels | notes")
    print("-" * 65)

    issues = []
    for gidx in range(CHECK_FROM, CHECK_TO + 1):
        off = GLYPH_REC_OFFSET + (gidx - 1) * 16
        xl = u16(data, off)
        xr = u16(data, off + 4)
        yb = u16(data, off + 6)
        bx = s16(data, off + 8)
        w  = u16(data, off + 10)
        ax = u16(data, off + 12)

        # derived
        y_top = yb - CELL_HEIGHT  # CELL_HEIGHT auto-detected from binary
        width_from_coords = xr - xl  # should equal w

        # check actual pixels in atlas
        has_pixels = False
        for x in range(xl, min(xr + 1, img.width)):
            for y in range(y_top, min(yb + 1, img.height)):
                if px[x, y][3] > 0:
                    has_pixels = True
                    break
            if has_pixels:
                break

        notes = []
        if width_from_coords != w:
            notes.append(f"xr-xl={width_from_coords} != w={w}")
        if xr >= img.width:
            notes.append(f"xr={xr} EXCEEDS atlas width {img.width}")
        if xl >= img.width:
            notes.append(f"xl={xl} EXCEEDS atlas width {img.width}")
        if ax == 0:
            notes.append("advance_x=0!")
        if not has_pixels and w > 0:
            notes.append("NO PIXELS in expected atlas area")

        tag = "HEB" if 122 <= gidx <= 148 else "   "
        pix_ok = "yes" if has_pixels else "NO!"
        note_str = ", ".join(notes) if notes else ""
        print(f" {gidx:3d} {tag} | xl={xl:4d} xr={xr:4d} bx={bx:3d} w={w:3d} ax={ax:3d} | {pix_ok}  | {note_str}")

        if notes:
            issues.append((gidx, notes))

    print()
    if issues:
        print(f"*** {len(issues)} ISSUES FOUND ***")
        for gidx, ns in issues:
            print(f"  glyph {gidx}: {'; '.join(ns)}")
    else:
        print("All glyphs OK.")

if __name__ == "__main__":
    main()

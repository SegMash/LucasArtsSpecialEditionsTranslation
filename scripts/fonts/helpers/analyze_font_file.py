"""
analyze_font_file.py - Reverse-engineer the .font binary format (final analysis).

CONFIRMED FORMAT:
  File size: always 19472 bytes
  Offset   0: uint32 = version (5)
  Offset   4: uint32 = num_glyphs (155)
  Offset   8: byte   = 34 (last char?)
  Offset   9: byte   = 33 ('!' = first char in primary char table)
  Offset  12: uint32 = cell_height_marker (= first_row_f3 value, differs per font)
  Offset  24: uint32 = same cell_height_marker repeated
  Offset  90: uint16[155] = PRIMARY char-code table, mapping
               codepoints 33..187  ->  glyph index (0 = no glyph)
  Offset 400: sparse extended char table (mostly zeros)
              Used for codepoints >= 188 and Windows-1252 codepoints 128-159
  Offset 16992 (= 19472 - 155*16): uint16[8][155] = glyph metric records
    Each 16-byte record for glyph G (1-based):
      [0] x_left    : pixel X of left edge in PNG (inclusive)
      [1] layer     : always 1
      [2] x_right   : pixel X of right edge in PNG (exclusive)
      [3] y_bottom  : pixel Y of row bottom (exclusive), same for all glyphs in row
      [4] bearing_x : signed horizontal pen offset before drawing (int16)
      [5] width     : glyph width in pixels (= x_right - x_left)
      [6] advance_x : pen advance after drawing
      [7] 0         : always zero
    y_top  of each row = y_bottom - cell_height
    cell_height = y_bottom of the FIRST glyph (all rows have equal height)

This script:
  A. Dumps all non-zero bytes in the extended char table region (400..16991)
     to reverse-engineer which codepoints map to glyphs 102-155.
  B. Prints a complete glyph manifest for MinisterT_24 with all fields decoded.
  C. Validates: for every glyph, checks that width == x_right - x_left.
"""

import struct
import os

FONTS_DIR        = r"c:\GOG Games\Monkey Island 2 SE\quickbms\output\fonts"
GLYPH_REC_OFFSET = 19472 - 155 * 16   # = 16992
CHAR_TABLE_OFFSET = 90
FIRST_CHAR        = 33   # '!'
NUM_CHARS         = 155

def read_font(name):
    return open(os.path.join(FONTS_DIR, name), "rb").read()

def u16(data, off):  return struct.unpack_from("<H", data, off)[0]
def s16(data, off):  return struct.unpack_from("<h", data, off)[0]

data24 = read_font("MinisterT_24.font")

# ── A. Dump sparse data in 400..16991 ─────────────────────────────────────────
print("=" * 72)
print("A. All non-zero bytes in the sparse region (offset 400..16991)")
print("   Glyph values 102-155 must be mapped here for codepoints >= 128")
print("=" * 72)

nz = [(off, data24[off]) for off in range(400, GLYPH_REC_OFFSET) if data24[off] != 0]
print(f"\n  Total non-zero bytes: {len(nz)}")
print(f"\n  {'offset':>8}  {'byte val':>10}  {'glyph idx':>10}  {'diff_from_prev':>14}")
prev_off = 400
for off, val in nz:
    print(f"  {off:8d}  {val:10d}  {val:10d}  {off-prev_off:14d}")
    prev_off = off
print()

# Analyze pattern: what is the stride/formula?
if nz:
    print("  Checking if offset = 400 + (glyph_index - 102) * stride:")
    first_off = nz[0][0]  # = 400
    first_val = nz[0][1]  # = 102
    # stride guess: spacing between consecutive entries
    if len(nz) >= 2:
        spacings = [nz[i+1][0] - nz[i][0] for i in range(min(10, len(nz)-1))]
        print(f"  Spacings between first 10 non-zero bytes: {spacings}")
    # Try to find a consistent formula
    print(f"\n  Trying offset = base + (glyph_idx - 102) * stride for various bases/strides:")
    for base_off in [400, 402, 404]:
        for stride in [6, 7, 8, 9, 10, 12, 14, 16]:
            matches = 0
            for off, val in nz:
                expected_off = base_off + (val - 102) * stride
                if expected_off == off:
                    matches += 1
            if matches == len(nz):
                print(f"    PERFECT MATCH: base={base_off}, stride={stride}  ({matches}/{len(nz)} matches)")
            elif matches > len(nz) // 2:
                print(f"    PARTIAL: base={base_off}, stride={stride}  ({matches}/{len(nz)} matches)")


# ── B. Complete glyph manifest for MinisterT_24 ───────────────────────────────
print()
print("=" * 72)
print("B. Complete glyph manifest for MinisterT_24")
print("=" * 72)

# Read char table (primary): codepoints 33-187
glyph_to_cp = {}   # glyph_idx -> codepoint
for i in range(NUM_CHARS):
    cp = FIRST_CHAR + i
    gidx = u16(data24, CHAR_TABLE_OFFSET + i*2)
    if gidx > 0 and gidx not in glyph_to_cp:
        glyph_to_cp[gidx] = cp

# Read glyph records and compute cell_height from first glyph
off0 = GLYPH_REC_OFFSET
cell_height = u16(data24, off0 + 6)   # f3 of first glyph = y_bottom of row 1

print(f"\n  cell_height = {cell_height}  (derived from f3 of first glyph)")
print(f"\n  {'G':>4}  {'cp':>5}  {'char':>6}  {'x_l':>5}  {'y_top':>6}  {'x_r':>5}  {'y_bot':>6}  {'bx':>4}  {'w':>4}  {'adv':>4}  {'row':>4}")

prev_y_bottom = 0
row_num = 0
for i in range(NUM_CHARS):
    gidx = i + 1   # 1-based
    off = GLYPH_REC_OFFSET + i * 16
    x_left   = u16(data24, off)
    layer    = u16(data24, off + 2)
    x_right  = u16(data24, off + 4)
    y_bottom = u16(data24, off + 6)
    bearing_x = s16(data24, off + 8)
    width    = u16(data24, off + 10)
    advance  = u16(data24, off + 12)
    zero     = u16(data24, off + 14)

    if y_bottom != prev_y_bottom:
        row_num += 1
        prev_y_bottom = y_bottom
    y_top = y_bottom - cell_height

    cp = glyph_to_cp.get(gidx, 0)
    if cp:
        ch = chr(cp) if 32 < cp < 127 else f"U+{cp:04X}"
    else:
        ch = "---"

    # Sanity check
    assert width == x_right - x_left, f"Glyph {gidx}: width mismatch {width} != {x_right}-{x_left}"

    print(f"  {gidx:4d}  {cp:5d}  {ch:>6}  {x_left:5d}  {y_top:6d}  {x_right:5d}  {y_bottom:6d}  {bearing_x:4d}  {width:4d}  {advance:4d}  {row_num:4d}")

print(f"\n  [OK] All width == x_right - x_left checks passed")


# ── C. Figure out extended char mappings by examining the sparse bytes ─────────
print()
print("=" * 72)
print("C. Reverse-engineering extended char mappings")
print("   For glyphs 102-155, what codepoints map to them?")
print("=" * 72)

# Strategy: the sparse bytes are at specific offsets. Check the "glyph_idx - some_base"
# against (offset - some_start) with various strides.
# From A, let's look at the raw offsets and values again
print(f"\n  Extended glyphs (102-155) and their codepoint mappings:")
print(f"  (glyphs 102-155 appear from roughly x=274 onwards in the last few rows)")
print()

# Check if the secondary table is a LOOKUP by codepoint:
# Try: glyph_to_cp extended by reading 1-byte entries at [off] where off-based indices
# For each non-zero byte at offset `off`:
# hypothesis: codepoint = some_function(off)
print("  Testing hypothesis: codepoint = 33 + (offset - 90) // stride")
for stride in [2, 4, 6, 8, 10]:
    all_match = True
    mapping = {}
    for off, val in nz:
        cp = 33 + (off - 90) // stride
        if 32 <= cp <= 256:
            mapping[val] = cp
        else:
            all_match = False
    if all_match and len(set(mapping.values())) == len(nz):
        print(f"  stride={stride}: ALL offsets map to valid codepoints, {len(mapping)} unique")
        for gidx in sorted(mapping.keys())[:20]:
            cp = mapping[gidx]
            ch = chr(cp) if 32 < cp < 127 else f"U+{cp:04X}"
            print(f"    glyph {gidx} -> cp={cp} ({ch})")
        break
    else:
        mismatches = sum(1 for off, _ in nz if not (32 <= 33 + (off-90)//stride <= 256))
        print(f"  stride={stride}: {mismatches} offsets out of range")

print()
print("  Trying direct byte-index: codepoint = offset (treating sparse byte AT codepoint offset):")
# Maybe: data24[codepoint] = glyph_index for codepoints 0-255
# If so, the table would start at offset 0 (or some base) and be indexed directly by codepoint
for base in [0, 88, 90]:
    hits = 0
    results = {}
    for off, val in nz:
        cp = off - base
        if 0 <= cp <= 255:
            hits += 1
            results[val] = cp
    if hits == len(nz):
        print(f"  base={base}: ALL {hits} non-zero entries map to codepoint = offset - {base}")
        for gidx in sorted(results.keys()):
            cp = results[gidx]
            ch = chr(cp) if 32 < cp < 256 else f"U+{cp:04X}"
            print(f"    glyph {gidx} -> cp={cp} ({ch})")
        break
    else:
        print(f"  base={base}: {hits}/{len(nz)} entries in range 0-255")

"""Analyze the gap pattern between glyphs in a .font file.

Usage: python analyze_gaps.py <font_path> [--count N]
"""
import struct, sys, os, argparse

FILE_SIZE = 19472
NUM_GLYPHS = 155
OFF = FILE_SIZE - NUM_GLYPHS * 16

def u16(d, o): return struct.unpack_from("<H", d, o)[0]
def s16(d, o): return struct.unpack_from("<h", d, o)[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("font_path")
    ap.add_argument("--count", type=int, default=60)
    args = ap.parse_args()

    data = open(args.font_path, "rb").read()
    cell_h = u16(data, OFF + 6)
    name   = os.path.basename(args.font_path)
    print(f"{name}  cell_height={cell_h}")
    print()
    print(f"  {'g':>4}  {'xl':>5}  {'xr':>5}  {'bx':>4}  {'gap_before':>10}  {'gap==bx?':>10}")

    prev_xr = None
    prev_yb = None
    mismatches = 0

    for i in range(min(args.count, NUM_GLYPHS)):
        off = OFF + i * 16
        xl  = u16(data, off)
        xr  = u16(data, off + 4)
        yb  = u16(data, off + 6)
        bx  = s16(data, off + 8)

        if prev_yb is not None and yb != prev_yb:
            print(f"  --- row break ---")
            prev_xr = None

        if prev_xr is not None:
            gap   = xl - prev_xr - 1
            match = "yes" if gap == bx else f"NO (gap={gap} bx={bx})"
            if gap != bx:
                mismatches += 1
        else:
            gap   = None
            match = "first"

        print(f"  g{i+1:3d}  {xl:5d}  {xr:5d}  {bx:4d}  {str(gap) if gap is not None else '':>10}  {match:>10}")
        prev_xr = xr
        prev_yb = yb

    print()
    print(f"Mismatches where gap != bearing_x: {mismatches}")

if __name__ == "__main__":
    main()

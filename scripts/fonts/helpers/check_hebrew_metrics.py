"""Quick check: print bearing_x and advance_x for Hebrew glyphs in a .font file."""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hebrew_mapping import HEBREW_TO_GLYPH

FILE_SIZE = 19472
NUM_GLYPHS = 155
GLYPH_REC_OFFSET = FILE_SIZE - NUM_GLYPHS * 16

def u16(d, o): return struct.unpack_from("<H", d, o)[0]
def s16(d, o): return struct.unpack_from("<h", d, o)[0]

def check(path, label):
    data = open(path, "rb").read()
    print(f"\n--- {label} ---")
    print(f"  {'Glyph':>6}  {'Letter':>6}  {'xl':>5}  {'xr':>5}  {'w':>4}  {'bx':>4}  {'ax':>4}")
    for letter, gidx in sorted(HEBREW_TO_GLYPH.items(), key=lambda x: x[1]):
        off = GLYPH_REC_OFFSET + (gidx - 1) * 16
        xl  = u16(data, off)
        xr  = u16(data, off + 4)
        bx  = s16(data, off + 8)
        w   = u16(data, off + 10)
        ax  = u16(data, off + 12)
        print(f"  {gidx:>6}  {letter:>6}  {xl:>5}  {xr:>5}  {w:>4}  {bx:>4}  {ax:>4}")

if __name__ == "__main__":
    check("quickbms/fonts/MinisterT_24.font",     "ORIGINAL  MinisterT_24")
    check("quickbms/fonts_heb/MinisterT_24.font", "REBUILT   MinisterT_24")

"""
find_render_routines.py - Locate the text/glyph rendering routines in Monkey2.exe

Strategy:
  1. Find "SpriteFont" and "TwoDee::Text" string references in .text code
  2. Walk backwards from each reference to find enclosing function start
  3. Scan for screen-width constants (640, 800, 1024, 1280) as int or float
  4. Search for x86 patterns consistent with coordinate arithmetic
     (sub reg, reg  /  neg + add  / movss xmm, [x]  / fld [x])
  5. Identify CALL sites near these regions — likely glyph draw helpers

Known facts about this exe:
  - Image base     : 0x00400000
  - TwoDee::Text   : VA 0x00530828, referenced from VA 0x004DCA00
  - SpriteFont     : VA 0x00530838, referenced from VA 0x004DCA0C
  - sprite2d.fx    : VA 0x00530448, referenced from VA 0x004D6069
  - Uses Direct3D9 (d3d9.dll) + D3DXCreateEffect shader
  - Coordinates handled via x87 FPU and SSE2 (movsd/movss)

Usage:
    python scripts/reverse-engineering/find_render_routines.py
    python scripts/reverse-engineering/find_render_routines.py --deep
"""

import struct
import os
import argparse

EXE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "Monkey2.exe")
IMAGE_BASE = 0x00400000

# --- PE helpers (minimal, mirroring pe_analysis.py) ---

def load_and_map(path=EXE_PATH):
    with open(path, "rb") as f:
        data = bytearray(f.read())

    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
    sec_base = pe_off + 24 + opt_sz

    sections = []
    for i in range(num_sec):
        s = sec_base + i * 40
        name = data[s:s+8].rstrip(b"\x00").decode("ascii", errors="replace")
        vaddr = struct.unpack_from("<I", data, s + 12)[0]
        raw_size = struct.unpack_from("<I", data, s + 16)[0]
        raw_off = struct.unpack_from("<I", data, s + 20)[0]
        sections.append((name, vaddr, raw_off, raw_size))

    def rva_to_off(rva):
        for _, va, ro, rs in sections:
            if va <= rva < va + rs:
                return ro + (rva - va)
        return None

    def off_to_va(off):
        for _, va, ro, rs in sections:
            if ro <= off < ro + rs:
                return IMAGE_BASE + va + (off - ro)
        return None

    def va_to_off(va):
        return rva_to_off(va - IMAGE_BASE)

    text_sec = next((s for s in sections if s[0] == ".text"), None)
    text_start = text_sec[2] if text_sec else 0
    text_end = text_start + (text_sec[3] if text_sec else 0)

    return data, off_to_va, va_to_off, text_start, text_end


# --- x86 helpers ---

def read_call_target(data, off):
    """If data[off] == 0xE8 (CALL rel32), return absolute target VA."""
    if data[off] != 0xE8:
        return None
    rel = struct.unpack_from("<i", data, off + 1)[0]
    caller_va = IMAGE_BASE + 0x1000 + (off - 0x400)  # approximate, works for .text
    next_ip = off + 5
    # More accurate: use off_to_va
    return None  # resolved externally


def find_function_start(data, off, search_back=512):
    """Walk backwards from `off` to find the nearest function prologue.
    Common x86 prologues:
      55 8B EC          push ebp; mov ebp, esp
      56 57 55 8B EC    push esi; push edi; push ebp; mov ebp, esp
      83 EC XX          sub esp, XX  (frameless)
    Also use CC (int3) padding as function boundary.
    """
    # Walk back looking for a run of 0xCC (padding) or common prologue bytes
    for back in range(0, search_back):
        candidate = off - back
        if candidate < 0:
            break
        b = data[candidate]
        # int3 padding after previous function
        if b == 0xCC:
            # function likely starts at candidate+1 (skip padding)
            start = candidate + 1
            while start < off and data[start] == 0xCC:
                start += 1
            return start
        # push ebp / mov ebp,esp
        if (b == 0x55 and
            candidate + 2 < len(data) and
            data[candidate+1] == 0x8B and
            data[candidate+2] == 0xEC):
            return candidate
        # sub esp, imm8 (83 EC xx)
        if b == 0x83 and candidate + 1 < len(data) and data[candidate+1] == 0xEC:
            return candidate
    return off  # fallback: return original offset


def decode_rel32_call(data, off, off_to_va):
    """Decode E8 rel32 at file offset `off`, return target VA."""
    if off + 5 > len(data) or data[off] != 0xE8:
        return None
    rel = struct.unpack_from("<i", data, off + 1)[0]
    caller_va = off_to_va(off)
    if caller_va is None:
        return None
    return (caller_va + 5 + rel) & 0xFFFFFFFF


def hex_dump_line(data, off, va, width=16):
    row = bytes(data[off:off+width])
    hex_part = " ".join(f"{b:02X}" for b in row)
    asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
    va_str = f"0x{va:08X}" if va else "        "
    return f"  {va_str}: {hex_part:<48}  {asc_part}"


def dump_region(data, off, va_base, length, off_to_va, label=""):
    if label:
        print(f"\n  --- {label} ---")
    for i in range(0, length, 16):
        va = off_to_va(off + i)
        print(hex_dump_line(data, off + i, va))


# --- Pattern search ---

# Float constants for common screen widths (IEEE-754 single precision)
SCREEN_WIDTH_FLOATS = {
    0x44A00000: "1280.0f",
    0x44800000: "1024.0f",
    0x44480000: "800.0f",
    0x44200000: "640.0f",
    0x447A0000: "1000.0f",
    0x44700000: "960.0f",
    0x44600000: "896.0f",
    0x449C0000: "1264.0f",
    0x449F0000: "1276.0f",
}

# Integer constants for screen widths (little-endian 4-byte)
SCREEN_WIDTH_INTS = {
    1280: "1280",
    1024: "1024",
    800:  "800",
    640:  "640",
    720:  "720",
    1920: "1920",
}

# x86 instruction byte patterns that suggest coordinate arithmetic
# These appear when computing  screen_width - x  or  x + glyph_width
COORD_PATTERNS = {
    # subss xmm0, xmm1  (F3 0F 5C C1) — float subtraction SSE
    bytes([0xF3, 0x0F, 0x5C]): "subss xmm,?",
    # subsd xmm0, xmm1  (F2 0F 5C C1)
    bytes([0xF2, 0x0F, 0x5C]): "subsd xmm,?",
    # fsub — x87 subtraction
    bytes([0xD8, 0x25]): "fsub [mem]",
    bytes([0xDE, 0xE9]): "fsubp",
    bytes([0xD8, 0xE1]): "fsub st1",
    # neg reg — negate register (used in width-x = -(x-width))
    bytes([0xF7, 0xD8]): "neg eax",
    bytes([0xF7, 0xD9]): "neg ecx",
    bytes([0xF7, 0xDA]): "neg edx",
    # addss / addsd (adding glyph width)
    bytes([0xF3, 0x0F, 0x58]): "addss xmm,?",
    bytes([0xF2, 0x0F, 0x58]): "addsd xmm,?",
}

# D3D9 device vtable method offsets (method_index * 4)
# Used to find Draw* calls even inside helper wrappers
D3D9_VTABLE_OFFSETS = {
    0x144: "IDirect3DDevice9::DrawPrimitive",
    0x148: "IDirect3DDevice9::DrawIndexedPrimitive",
    0x14C: "IDirect3DDevice9::DrawPrimitiveUP",
    0x150: "IDirect3DDevice9::DrawIndexedPrimitiveUP",
    0x104: "IDirect3DDevice9::SetTexture",
    0x164: "IDirect3DDevice9::SetFVF",
    0x190: "IDirect3DDevice9::SetStreamSource",
    0x10C: "IDirect3DDevice9::SetRenderState",
    0x50:  "IDirect3DDevice9::Present",
    0x44:  "IDirect3DDevice9::SetTransform",
}

# ModRM bytes for call [reg+disp32] : FF /2 modrm disp32
# Registers: eax=0x90, ecx=0x91, edx=0x92, ebx=0x93, esi=0x96, edi=0x97
REG_NAMES = {0x90: "eax", 0x91: "ecx", 0x92: "edx", 0x93: "ebx", 0x96: "esi", 0x97: "edi"}


def search_d3d9_vtable_calls(data, text_start, text_end, off_to_va):
    """Find all D3D9 vtable call sites in .text."""
    results = {}
    for vtoff, name in D3D9_VTABLE_OFFSETS.items():
        offbytes = struct.pack("<I", vtoff)
        sites = []
        for modrm in REG_NAMES:
            pattern = bytes([0xFF, modrm]) + offbytes
            idx = text_start
            while True:
                idx = data.find(pattern, idx, text_end)
                if idx < 0:
                    break
                sites.append((off_to_va(idx), modrm, idx))
                idx += 6
        results[name] = sites
    return results


def search_screen_width_constants(data, text_start, text_end, off_to_va):
    """Find places where screen width constants appear as immediate or memory values."""
    found = []
    for fval, label in SCREEN_WIDTH_FLOATS.items():
        fb = struct.pack("<I", fval)
        idx = text_start
        while True:
            idx = data.find(fb, idx, text_end)
            if idx < 0:
                break
            found.append(dict(va=off_to_va(idx), file_off=idx, value=label, kind="float"))
            idx += 4
    for ival, label in SCREEN_WIDTH_INTS.items():
        ib = struct.pack("<I", ival)
        idx = text_start
        while True:
            idx = data.find(ib, idx, text_end)
            if idx < 0:
                break
            found.append(dict(va=off_to_va(idx), file_off=idx, value=label, kind="int"))
            idx += 4
    return found


def find_xrefs_to_va(data, target_va, text_start, text_end, off_to_va):
    """Find all places in .text that hold a 4-byte pointer to target_va."""
    tb = struct.pack("<I", target_va)
    refs = []
    idx = text_start
    while True:
        idx = data.find(tb, idx, text_end)
        if idx < 0:
            break
        refs.append(dict(file_off=idx, va=off_to_va(idx)))
        idx += 4
    return refs


def find_calls_in_region(data, start_off, end_off, off_to_va):
    """Find all CALL rel32 (E8 xx xx xx xx) in region, decode targets."""
    calls = []
    for off in range(start_off, min(end_off - 4, len(data) - 5)):
        if data[off] == 0xE8:
            target = decode_rel32_call(data, off, off_to_va)
            if target and IMAGE_BASE <= target < IMAGE_BASE + 0x200000:
                calls.append(dict(site_off=off, site_va=off_to_va(off), target_va=target))
    return calls


def main():
    parser = argparse.ArgumentParser(description="Find text/glyph rendering routines in Monkey2.exe")
    parser.add_argument("--deep", action="store_true", help="Also scan full .text for D3D9 patterns")
    parser.add_argument("--exe", default=EXE_PATH, help="Path to exe")
    args = parser.parse_args()

    print(f"Loading {args.exe} ...")
    data, off_to_va, va_to_off, text_start, text_end = load_and_map(args.exe)
    print(f"  .text: file 0x{text_start:08X} - 0x{text_end:08X}  ({(text_end-text_start)//1024} KB)")

    # ----------------------------------------------------------------
    # 1. Known anchor points — string xrefs
    # ----------------------------------------------------------------
    print("\n" + "="*60)
    print("  ANCHOR POINTS: String cross-references")
    print("="*60)

    anchors = {
        "TwoDee::Text":  0x00530828,
        "SpriteFont":    0x00530838,
        "sprite2d.fx":   0x00530448,
        "fontDirectory": 0x00530054,
        "fonts/fonts.dir": 0x00530194,
        "TwoDee::Text xref": None,  # will be found
    }

    anchor_code_offsets = {}
    for label, str_va in anchors.items():
        if str_va is None:
            continue
        refs = find_xrefs_to_va(data, str_va, text_start, text_end, off_to_va)
        if refs:
            for r in refs:
                fn_start = find_function_start(data, r["file_off"])
                fn_va = off_to_va(fn_start)
                anchor_code_offsets[label] = fn_start
                print(f"\n  '{label}' @ VA 0x{str_va:08X}")
                print(f"    Referenced @ code VA 0x{r['va']:08X} (file 0x{r['file_off']:08X})")
                print(f"    Enclosing function starts ~ VA 0x{fn_va:08X} (file 0x{fn_start:08X})")
                # Dump 48 bytes around reference
                dump_region(data, r["file_off"]-8, None, 64, off_to_va, "Code context")
        else:
            print(f"\n  '{label}' @ VA 0x{str_va:08X} — NO XREFS FOUND in .text")

    # ----------------------------------------------------------------
    # 2. Screen width constant search
    # ----------------------------------------------------------------
    print("\n" + "="*60)
    print("  SCREEN WIDTH CONSTANTS IN .text")
    print("="*60)
    width_hits = search_screen_width_constants(data, text_start, text_end, off_to_va)
    print(f"  Found {len(width_hits)} hits")
    for h in width_hits[:30]:
        # Show surrounding bytes to understand instruction context
        off = h["file_off"]
        ctx_before = bytes(data[max(off-4,0):off])
        ctx_after  = bytes(data[off:off+4])
        print(f"  VA=0x{h['va']:08X}  {h['kind']:<6} {h['value']:<10}  "
              f"ctx: ...{ctx_before.hex()} | {ctx_after.hex()}...")

    # ----------------------------------------------------------------
    # 3. D3D9 vtable calls (full exe scan)
    # ----------------------------------------------------------------
    if args.deep:
        print("\n" + "="*60)
        print("  D3D9 VTABLE CALLS (full .text scan)")
        print("="*60)
        d3d9_calls = search_d3d9_vtable_calls(data, text_start, text_end, off_to_va)
        for method, sites in d3d9_calls.items():
            if sites:
                print(f"\n  {method}: {len(sites)} call site(s)")
                for va, modrm, file_off in sites[:5]:
                    fn_start = find_function_start(data, file_off)
                    fn_va = off_to_va(fn_start)
                    print(f"    call [{REG_NAMES[modrm]}+{D3D9_VTABLE_OFFSETS[struct.unpack_from('<I',data,file_off+2)[0]]:s}]  "
                          f"@ VA=0x{va:08X}  in func~0x{fn_va:08X}")

    # ----------------------------------------------------------------
    # 4. CALL targets within the SpriteFont/TwoDee::Text vicinity
    # ----------------------------------------------------------------
    print("\n" + "="*60)
    print("  CALL GRAPH: Near SpriteFont / TwoDee::Text")
    print("="*60)

    for label, fn_off in anchor_code_offsets.items():
        region_end = fn_off + 512
        calls = find_calls_in_region(data, fn_off, region_end, off_to_va)
        if calls:
            print(f"\n  Calls from '{label}' function region (0x{off_to_va(fn_off):08X}):")
            for c in calls[:12]:
                print(f"    VA=0x{c['site_va']:08X}  ->  target=0x{c['target_va']:08X}")

    # ----------------------------------------------------------------
    # 5. Coordinate pattern search near anchors
    # ----------------------------------------------------------------
    print("\n" + "="*60)
    print("  COORDINATE PATTERNS near anchor regions")
    print("="*60)

    for label, fn_off in anchor_code_offsets.items():
        region_start = max(text_start, fn_off - 64)
        region_end = min(text_end, fn_off + 1024)
        hits = []
        for pattern, pname in COORD_PATTERNS.items():
            idx = region_start
            while True:
                idx = data.find(pattern, idx, region_end)
                if idx < 0:
                    break
                hits.append((off_to_va(idx), pname, idx))
                idx += len(pattern)
        if hits:
            print(f"\n  Coord patterns near '{label}':")
            for va, pname, off in sorted(hits, key=lambda x: x[0])[:10]:
                ctx = bytes(data[off:off+8])
                print(f"    VA=0x{va:08X}  {pname:<20}  bytes: {ctx.hex()}")

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    print("\n" + "="*60)
    print("  SUMMARY / NEXT STEPS")
    print("="*60)
    print("""
  Key addresses to investigate in Ghidra or x32dbg:

    0x004DCA00  — TwoDee::Text class registration / ctor vicinity
    0x004DCA0C  — SpriteFont string reference
    0x004D6069  — sprite2d.fx shader load (renderer init)
    0x004C63E3  — fontDirectory config key read

  Recommended workflow:
    1. Open Monkey2.exe in Ghidra (File > Import File, select PE format x86)
    2. Run auto-analysis (accept defaults)
    3. Search for string "SpriteFont" (Search > For String)
    4. Double-click the xref to reach the class registration code
    5. Right-click the function -> "References -> Show References to Function"
    6. Look for the Draw/Render virtual method — it will call into sprite2d
    7. Find where X coordinate is pushed/passed — that is the patch site

  For x32dbg (dynamic):
    1. Launch game, attach x32dbg to Monkey2.exe
    2. Open Symbols tab, navigate to d3d9.dll
    3. Search inside d3d9.dll for the vtable of IDirect3DDevice9
    4. Set breakpoint on IDirect3DDevice9::DrawPrimitive (or DrawPrimitiveUP)
    5. When a text character renders, examine the call stack
    6. The frame that set up x/y vertex coordinates is your target

  See WORKFLOW.md for the full step-by-step patching guide.
""")


if __name__ == "__main__":
    main()

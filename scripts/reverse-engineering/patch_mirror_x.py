"""
patch_mirror_x.py - Binary patcher for mirroring the x-coordinate in Monkey2.exe

Goal: Change the letter-draw routine so that instead of rendering a glyph at
position (x, y) it renders it at (screen_width - x, y).  This enables right-to-
left rendering for Hebrew text.

WORKFLOW:
  Step 1.  Find the patch site using find_render_routines.py + Ghidra/x32dbg.
  Step 2.  Fill in PATCH_SITE_VA and PATCH_BYTES (or SHELLCODE) below.
  Step 3.  Run:  python scripts/reverse-engineering/patch_mirror_x.py --apply
  Step 4.  Test the game.
  Step 5.  If wrong:  python scripts/reverse-engineering/patch_mirror_x.py --restore

The script also provides helpers:
  --list-patches   Show all configured patches and their current status
  --verify         Check whether patches are applied or original
  --dump-site VA   Hex-dump the patch area (useful to confirm bytes)

Usage examples:
  python scripts/reverse-engineering/patch_mirror_x.py --list-patches
  python scripts/reverse-engineering/patch_mirror_x.py --apply
  python scripts/reverse-engineering/patch_mirror_x.py --restore
  python scripts/reverse-engineering/patch_mirror_x.py --dump-site 0x004DCA30
  python scripts/reverse-engineering/patch_mirror_x.py --apply --patch-id 0
"""

import struct
import os
import shutil
import argparse
from datetime import datetime

EXE_PATH   = os.path.join(os.path.dirname(__file__), "..", "..", "Monkey2.exe")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backup")
IMAGE_BASE = 0x00400000

# ---------------------------------------------------------------------------
# PATCH DEFINITIONS
#
# Each entry describes one binary patch.  Fill these in after identifying the
# exact patch site via reverse engineering.
#
# Fields:
#   id          Unique name for this patch
#   description Human-readable description
#   va          Virtual address (in exe's address space) of the first byte to patch
#   original    Original bytes at that address (used to verify / restore)
#   patched     Replacement bytes that implement  x = screen_width - x
#
# HOW TO DERIVE patched bytes:
#   Suppose the draw-char function receives x in eax (or xmm0 for SSE).
#   The "subtract from screen width" operation in x86 can be done as:
#
#   Option A — integer x in a register (e.g. eax):
#     Before patch:   mov [esp+8], eax          ; store x arg
#     After patch:    neg eax
#                     add eax, <screen_width>   ; = width - x
#                     mov [esp+8], eax
#     If there is not enough room, redirect to a code cave (see CAVE_PATCHES).
#
#   Option B — float x in xmm0 (SSE):
#     Before:  movss [esp+8], xmm0
#     After:   use subss with a screen-width constant
#
#   Option C — inject a whole new instruction sequence in a code cave and
#     replace the original with a JMP rel32 (5 bytes).
#
# IMPORTANT: original and patched must be the same length.
#            Use NOP (0x90) to pad if needed.
# ---------------------------------------------------------------------------

PATCHES = [
    # -----------------------------------------------------------------------
    # VERIFIED PATCH — mirrors x inside DrawChar (0x004DA6D0)
    #
    # DrawChar confirmed by:
    #   imul edi, edi, 0x8C  (140-byte glyph table stride) at 0x004DA6E7
    #   Only 2 callers (0x004DE826, 0x004DEC14), both in SpriteFont vtable
    #   SpriteFont vtable @ 0x0053091C  registered as "TwoDee::Text"
    #
    # x-coordinate (arg7) loaded at:
    #   0x004DA8A5: F3 0F 10 8C 24 3C 01 00 00  movss xmm1, [esp+0x13C]
    #
    # Patch replaces that 9-byte instruction with JMP into NOP cave,
    # computes  xmm1 = 1280.0f - xmm1,  then JMPs back.
    #
    # Cave: 0x004D3B0C — 55-byte NOP sled, permanently skipped by JMP at 0x004D3B0A.
    #
    # Cave layout (33 bytes at 0x004D3B0C):
    #   004D3B0C: 00 00 A0 44             -- float 1280.0f (data, not executed by CPU)
    #   004D3B10: F3 0F 10 8C 24 3C 01 00 00  -- movss xmm1,[esp+0x13C]  (orig insn)
    #   004D3B19: F3 0F 10 15 0C 3B 4D 00     -- movss xmm2,[0x004D3B0C] ; load 1280
    #   004D3B21: F3 0F 5C D1                 -- subss xmm2, xmm1         ; 1280 - x
    #   004D3B25: 0F 28 CA                    -- movaps xmm1, xmm2         ; result -> xmm1
    #   004D3B28: E9 81 6D 00 00             -- jmp 0x004DA8AE             ; return
    #
    # Patch at 0x004DA8A5 (9 bytes):
    #   Original: F3 0F 10 8C 24 3C 01 00 00  movss xmm1, [esp+0x13C]
    #   Patched:  E9 66 92 FF FF 90 90 90 90  jmp 0x004D3B10 + 4×NOP
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # WRONG PATCH (patched UV atlas coords, not screen X) — DISABLED
    # -----------------------------------------------------------------------
    dict(
        id="mirror_x_drawchar_wrong",
        description=(
            "WRONG: patched [esp+0x13C] which is a UV atlas coord, not screen X.\n"
            "  Disabled in favour of mirror_x_drawchar_v2."
        ),
        va=0x004DA8A5,
        original=bytes.fromhex("F30F108C243C010000"),  # movss xmm1, [esp+0x13C]
        patched= bytes.fromhex("E96692FFFF90909090"),  # jmp 0x004D3B10 + 4×NOP
        enabled=False,
    ),
    # -----------------------------------------------------------------------
    # CORRECT PATCH: screen X is loaded at 0x004DA7C0 as movss xmm2,[esp+0x144]
    #
    # Why 0x144 not 0x13C?
    #   Prologue: sub esp,0x114 + push ebx + push ebp + push esi + push edi = 0x124 shift.
    #   Screen X is arg at [entry_esp+0x20], so inside function: [esp+0x20+0x124]=[esp+0x144].
    #
    # Cave layout at 0x004D3B0C (33 bytes, fits in 55-byte NOP sled):
    #   004D3B0C: 00 00 A0 44                -- float 1280.0f (data, not executed)
    #   004D3B10: F3 0F 10 94 24 44 01 00 00 -- movss xmm2,[esp+0x144]  (original insn)
    #   004D3B19: F3 0F 10 35 0C 3B 4D 00   -- movss xmm6,[0x004D3B0C] ; load 1280.0f
    #   004D3B21: F3 0F 5C F2               -- subss xmm6, xmm2         ; 1280 - x
    #   004D3B25: 0F 28 D6                  -- movaps xmm2, xmm6         ; result -> xmm2
    #   004D3B28: E9 9C 6C 00 00            -- jmp 0x004DA7C9            ; return
    #
    # Patch at 0x004DA7C0 (9 bytes):
    #   Original: F3 0F 10 94 24 44 01 00 00  movss xmm2, [esp+0x144]
    #   Patched:  E9 4B 93 FF FF 90 90 90 90  jmp 0x004D3B10 + 4×NOP
    # -----------------------------------------------------------------------
    dict(
        id="mirror_x_drawchar_v2",
        description=(
            "CURSOR ONLY — patches DrawChar(0x004DA6D0), used for cursor sprite.\n"
            "  Keep DISABLED to avoid inverting mouse cursor controls.\n"
            "  Apply together with mirror_x_cave_v2."
        ),
        va=0x004DA7C0,
        original=bytes.fromhex("F30F10942444010000"),  # movss xmm2, [esp+0x144]
        patched= bytes.fromhex("E94B93FFFF90909090"),   # jmp 0x004D3B10 + 4×NOP
        enabled=False,
    ),
    dict(
        id="mirror_x_cave_v2",
        description=(
            "Cave for drawchar_v2 (cursor). Disabled.\n"
            "  Apply together with mirror_x_drawchar_v2."
        ),
        va=0x004D3B0C,
        original=bytes.fromhex("90" * 33),  # 33 NOPs from the sled
        patched=bytes.fromhex(
            "0000A044"                # [004D3B0C] float 1280.0f (data)
            "F30F10942444010000"      # [004D3B10] movss xmm2,[esp+0x144]  (original insn)
            "F30F10350C3B4D00"        # [004D3B19] movss xmm6,[0x004D3B0C] ; load 1280.0f
            "F30F5CF2"                # [004D3B21] subss xmm6, xmm2         ; 1280 - x
            "0F28D6"                  # [004D3B25] movaps xmm2, xmm6         ; result -> xmm2
            "E99C6C0000"              # [004D3B28] jmp 0x004DA7C9            ; return
        ),
        enabled=False,
    ),
    # -----------------------------------------------------------------------
    # TEXT PATCH v3: target the 2 TEXT-SPECIFIC call sites that push X=[esp+4]
    # before calling 0x004DAC60 (the text draw wrapper).
    #
    # Confirmed text-only callers (not sprites):
    #   0x004DC1EA  -- TwoDee::Text character rendering loop
    #   0x004DEA01  -- DrawString per-character call
    #
    # Both set [esp+4] = screen X just before the call.
    # We intercept at that X-store point and compute 1280-X instead.
    #
    # Cave layout at 0x004D3B0C (55 bytes total):
    #
    #  [004D3B0C] 00 00 A0 44                    -- float 1280.0f  (DATA, 4 bytes)
    #
    #  --- CAVE 1 (for site 0x004DC1DB, 21 bytes) ---------------
    #  [004D3B10] D9 44 24 4C                    -- fld  [esp+0x4C]      ; reload X (original insn 1)
    #  [004D3B14] D9 05 0C 3B 4D 00              -- fld  [0x4D3B0C]      ; push 1280.0f
    #  [004D3B1A] DE E9                          -- fsubrp st(1),st(0)   ; st0 = 1280-X
    #  [004D3B1C] D9 5C 24 04                    -- fstp [esp+0x04]      ; store (original insn 2)
    #  [004D3B20] E9 BE 86 00 00                 -- jmp 0x004DC1E3       ; return
    #
    #  --- CAVE 2 (for site 0x004DE9F4, 26 bytes) ---------------
    #  [004D3B25] F3 0F 10 35 0C 3B 4D 00        -- movss xmm6,[0x4D3B0C]; xmm6=1280
    #  [004D3B2D] F3 0F 5C F0                    -- subss xmm6,xmm0     ; xmm6=1280-X
    #  [004D3B31] 0F 28 C6                       -- movaps xmm0,xmm6    ; xmm0=1280-X
    #  [004D3B34] F3 0F 11 44 24 04              -- movss [esp+0x04],xmm0; store (original insn)
    #  [004D3B3A] E9 BB AE 00 00                 -- jmp 0x004DE9FA       ; return
    #  [004D3B3F] 90 90 90 90                    -- padding
    #
    # Patch site 1 at 0x004DC1DB (8 bytes):
    #   Original: D9 44 24 4C  D9 5C 24 04  (fld [esp+0x4C]; fstp [esp+0x04])
    #   Patched:  E9 30 79 FF FF  90 90 90  (jmp 0x004D3B10 + 3×NOP)
    #
    # Patch site 2 at 0x004DE9F4 (6 bytes):
    #   Original: F3 0F 11 44 24 04           (movss [esp+0x04], xmm0)
    #   Patched:  E9 2C 51 FF FF  90          (jmp 0x004D3B25 + 1×NOP)
    # -----------------------------------------------------------------------
    dict(
        id="mirror_x_text_site1",
        description=(
            "Mirror X at TwoDee::Text character loop (0x004DC1DB).\n"
            "  Intercepts fld [esp+0x4C]; fstp [esp+0x04] — the X push before call.\n"
            "  Apply together with mirror_x_text_cave_v3."
        ),
        va=0x004DC1DB,
        original=bytes.fromhex("D944244CD95C2404"),  # fld [esp+0x4C]; fstp [esp+0x04]
        patched= bytes.fromhex("E93079FFFF909090"),  # jmp 0x004D3B10 + 3×NOP
        enabled=True,
    ),
    dict(
        id="mirror_x_text_site2",
        description=(
            "Mirror X at DrawString per-char call (0x004DE9F4).\n"
            "  Intercepts movss [esp+0x04],xmm0 — the X push before call.\n"
            "  Apply together with mirror_x_text_cave_v3."
        ),
        va=0x004DE9F4,
        original=bytes.fromhex("F30F11442404"),   # movss [esp+0x04], xmm0
        patched= bytes.fromhex("E92C51FFFF90"),   # jmp 0x004D3B25 + 1×NOP
        enabled=True,
    ),
    dict(
        id="mirror_x_text_cave_v3",
        description=(
            "Cave at 0x004D3B0C: two sub-caves mirroring X for both text call sites.\n"
            "  Cave1 (x87 FPU): 0x004D3B10-24 for site1.\n"
            "  Cave2 (SSE):     0x004D3B25-3E for site2."
        ),
        va=0x004D3B0C,
        original=bytes.fromhex(
            "0000A044"
            "F30F10942444010000"
            "F30F10350C3B4D00"
            "F30F5CF2"
            "0F28D6"
            "E99C6C0000"
            "90" * 22
        ),
        patched=bytes.fromhex(
            "0000A044"                # [004D3B0C] float 1280.0f  (DATA)
            "D944244C"                # [004D3B10] fld [esp+0x4C]       ; X → ST0
            "D9050C3B4D00"            # [004D3B14] fld [0x4D3B0C]       ; 1280 → ST0
            "DEE9"                    # [004D3B1A] fsubrp st(1),st(0)   ; ST0=1280-X
            "D95C2404"                # [004D3B1C] fstp [esp+0x04]      ; store
            "E9BE860000"              # [004D3B20] jmp 0x004DC1E3
            "F30F10350C3B4D00"        # [004D3B25] movss xmm6,[1280.0f]
            "F30F5CF0"                # [004D3B2D] subss xmm6,xmm0
            "0F28C6"                  # [004D3B31] movaps xmm0,xmm6
            "F30F11442404"            # [004D3B34] movss [esp+0x04],xmm0 (original insn)
            "E9BBAE0000"              # [004D3B3A] jmp 0x004DE9FA
            "90909090"                # [004D3B3F] padding
        ),
        enabled=True,
    ),
]

CAVE_PATCHES = []  # now folded into PATCHES above


# ---------------------------------------------------------------------------
# PE helpers
# ---------------------------------------------------------------------------

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
        vaddr = struct.unpack_from("<I", data, s+12)[0]
        raw_size = struct.unpack_from("<I", data, s+16)[0]
        raw_off = struct.unpack_from("<I", data, s+20)[0]
        sections.append((name, vaddr, raw_off, raw_size))

    def va_to_off(va):
        rva = va - IMAGE_BASE
        for _, vaddr, ro, rs in sections:
            if vaddr <= rva < vaddr + rs:
                return ro + (rva - vaddr)
        return None

    def off_to_va(off):
        for _, va, ro, rs in sections:
            if ro <= off < ro + rs:
                return IMAGE_BASE + va + (off - ro)
        return None

    return data, va_to_off, off_to_va


def make_backup(exe_path=EXE_PATH):
    import stat
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"Monkey2_pre_patch_{ts}.exe")
    shutil.copy2(exe_path, dest)
    # Ensure the backup copy is writable
    try:
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IWRITE)
    except OSError:
        pass
    print(f"  Backup saved: {dest}")
    return dest


def write_exe(data, exe_path=EXE_PATH):
    import stat
    # Clear read-only flag if set
    try:
        current_mode = os.stat(exe_path).st_mode
        if not (current_mode & stat.S_IWRITE):
            os.chmod(exe_path, current_mode | stat.S_IWRITE)
            print(f"  Cleared read-only flag on {exe_path}")
    except OSError:
        pass
    try:
        with open(exe_path, "wb") as f:
            f.write(data)
        print(f"  Written: {exe_path}")
    except PermissionError:
        print()
        print("  ERROR: PermissionError — cannot write to:")
        print(f"    {exe_path}")
        print()
        print("  This is a Windows UAC / folder-protection issue.")
        print("  Solutions:")
        print("   1. Run this script from an elevated prompt:")
        print("        Right-click 'Command Prompt' or 'PowerShell' -> 'Run as administrator'")
        print("        then re-run:  python patch_mirror_x.py --apply")
        print()
        print("   2. Or patch a copy in a writable location:")
        print(f"        copy \"{exe_path}\" \"%USERPROFILE%\\Desktop\\Monkey2.exe\"")
        print("        python patch_mirror_x.py --apply --exe \"%USERPROFILE%\\Desktop\\Monkey2.exe\"")
        print("        then copy the patched file back (as admin)")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Patch operations
# ---------------------------------------------------------------------------

def verify_patch(data, va_to_off, patch):
    """Return 'original', 'patched', or 'unknown'."""
    if patch["va"] is None or patch["original"] is None:
        return "unconfigured"
    off = va_to_off(patch["va"])
    if off is None:
        return "bad_va"
    current = bytes(data[off : off + len(patch["original"])])
    if current == patch["original"]:
        return "original"
    if patch["patched"] and current == patch["patched"]:
        return "patched"
    return f"unknown ({current.hex()})"


def apply_patch(data, va_to_off, patch):
    """Apply patch in-place. Returns True on success."""
    off = va_to_off(patch["va"])
    if off is None:
        print(f"  ERROR: cannot resolve VA 0x{patch['va']:08X}")
        return False
    current = bytes(data[off : off + len(patch["original"])])
    if current != patch["original"]:
        print(f"  ERROR: bytes at 0x{patch['va']:08X} do not match original")
        print(f"    Expected : {patch['original'].hex()}")
        print(f"    Found    : {current.hex()}")
        return False
    data[off : off + len(patch["patched"])] = patch["patched"]
    print(f"  Patched VA=0x{patch['va']:08X}  {patch['original'].hex()} -> {patch['patched'].hex()}")
    return True


def restore_patch(data, va_to_off, patch):
    """Restore original bytes. Returns True on success."""
    off = va_to_off(patch["va"])
    if off is None:
        print(f"  ERROR: cannot resolve VA 0x{patch['va']:08X}")
        return False
    current = bytes(data[off : off + len(patch["patched"])])
    if current != patch["patched"]:
        print(f"  WARNING: bytes do not match patched state (already restored?)")
        print(f"    Expected patched: {patch['patched'].hex()}")
        print(f"    Found           : {current.hex()}")
    data[off : off + len(patch["original"])] = patch["original"]
    print(f"  Restored VA=0x{patch['va']:08X}")
    return True


def hex_dump(data, off, length, off_to_va, label=""):
    if label:
        print(f"\n  --- {label} ---")
    for i in range(0, length, 16):
        row = bytes(data[off+i : off+i+16])
        va = off_to_va(off + i)
        va_s = f"0x{va:08X}" if va else "        "
        hex_p = " ".join(f"{b:02X}" for b in row)
        asc_p = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  {va_s}: {hex_p:<48}  {asc_p}")


# ---------------------------------------------------------------------------
# Screen-width constant builder — generates x86 bytes for common transforms
# ---------------------------------------------------------------------------

def build_mirror_x_int_eax(screen_width=1280):
    """
    Build x86-32 bytes that compute eax = screen_width - eax.
    Sequence:
        neg eax          ; eax = -eax
        add eax, W       ; eax = W - original_eax
    Total: 2 + 5 = 7 bytes
    """
    neg_eax = bytes([0xF7, 0xD8])
    add_eax = bytes([0x05]) + struct.pack("<I", screen_width)
    return neg_eax + add_eax


def build_mirror_x_xmm0_float(screen_width=1280.0):
    """
    Build x86-32 SSE bytes that compute xmm0 = screen_width - xmm0.
    Requires screen_width to be in memory as a float constant (code cave).

    The simplest in-place approach:
        movss xmm1, [rel_to_constant]   ; xmm1 = screen_width (float)
        subss xmm1, xmm0                ; xmm1 = width - x
        movaps xmm0, xmm1               ; xmm0 = result

    NOTE: This needs a nearby float constant; fill the relative address after
    placing the constant in a code cave.
    """
    # Placeholder — actual bytes depend on where the float constant lives
    # movss xmm1, [eip-relative — NOT available in x86-32, need absolute addr]
    # This is built at patch time when you know the cave address.
    sw_bytes = struct.pack("<f", screen_width)
    print(f"  screen_width {screen_width} as float bytes: {sw_bytes.hex()}")
    return None  # must be generated with actual addresses


def find_code_cave(data, min_size=16, fill_byte=None):
    """Find runs of repeating filler bytes (INT3=0xCC or NOP=0x90) in .text.
    If fill_byte is None, search for both 0xCC and 0x90."""
    caves = []
    text_start = 0x400
    text_end = text_start + 1105920
    search_bytes = [fill_byte] if fill_byte else [0xCC, 0x90]

    for fb in search_bytes:
        run_start = None
        run_len = 0
        for i in range(text_start, text_end):
            if data[i] == fb:
                if run_start is None:
                    run_start = i
                run_len += 1
            else:
                if run_len >= min_size:
                    caves.append((run_start, run_len, fb))
                run_start = None
                run_len = 0
        if run_len >= min_size:
            caves.append((run_start, run_len, fb))

    return caves


def add_cave_section(exe_path, cave_code, section_name=b".cave"):
    """
    Append a new executable PE section containing cave_code to the exe.
    Returns (new_exe_data, new_section_va) or (None, None) on error.

    The new section is added after all existing sections, with:
      - Characteristics: CODE | EXECUTE | READ  (0x60000020)
      - VirtualSize / RawSize: aligned to 0x1000 / 0x200
    """
    with open(exe_path, "rb") as f:
        data = bytearray(f.read())

    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
    opt_off = pe_off + 24

    # Get existing image size and section alignment
    sec_align = struct.unpack_from("<I", data, opt_off + 32)[0]  # SectionAlignment
    file_align = struct.unpack_from("<I", data, opt_off + 36)[0]  # FileAlignment
    image_size = struct.unpack_from("<I", data, opt_off + 56)[0]

    # Last section's end gives us the next available VirtualAddress
    sec_hdr_base = opt_off + opt_sz
    last_sec = sec_hdr_base + (num_sec - 1) * 40
    last_va    = struct.unpack_from("<I", data, last_sec + 12)[0]
    last_vsz   = struct.unpack_from("<I", data, last_sec + 8)[0]
    last_roff  = struct.unpack_from("<I", data, last_sec + 20)[0]
    last_rsz   = struct.unpack_from("<I", data, last_sec + 16)[0]

    def align_up(val, align):
        return (val + align - 1) & ~(align - 1)

    new_va   = align_up(last_va + last_vsz, sec_align)
    new_vsz  = align_up(len(cave_code), sec_align)
    new_roff = align_up(last_roff + last_rsz, file_align)
    new_rsz  = align_up(len(cave_code), file_align)

    # Pad cave_code to raw size
    cave_padded = cave_code + bytes(new_rsz - len(cave_code))

    # Build new section header (40 bytes)
    name_bytes = section_name[:8].ljust(8, b"\x00")
    new_hdr = (
        name_bytes +
        struct.pack("<I", new_vsz) +   # VirtualSize
        struct.pack("<I", new_va) +    # VirtualAddress
        struct.pack("<I", new_rsz) +   # SizeOfRawData
        struct.pack("<I", new_roff) +  # PointerToRawData
        b"\x00" * 12 +                # PointerToRelocations etc.
        struct.pack("<I", 0x60000020)  # CODE|EXEC|READ
    )

    # Patch num sections and image size
    struct.pack_into("<H", data, pe_off + 6, num_sec + 1)
    struct.pack_into("<I", data, opt_off + 56, align_up(new_va + new_vsz, sec_align))

    # Insert new section header right after existing ones
    insert_pos = sec_hdr_base + num_sec * 40
    data = data[:insert_pos] + new_hdr + data[insert_pos:]

    # Append cave bytes at end
    # Pad file to new_roff if needed
    if len(data) < new_roff:
        data += bytes(new_roff - len(data))
    data += cave_padded

    new_section_va = IMAGE_BASE + new_va
    print(f"  New section VA: 0x{new_section_va:08X}  raw_off: 0x{new_roff:08X}  size: {new_rsz}")
    return bytes(data), new_section_va


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Binary patcher for Monkey2.exe — mirror x-coordinate for RTL rendering"
    )
    parser.add_argument("--apply",        action="store_true", help="Apply all enabled patches")
    parser.add_argument("--restore",      action="store_true", help="Restore all patched bytes to original")
    parser.add_argument("--verify",       action="store_true", help="Check patch status without modifying")
    parser.add_argument("--list-patches", action="store_true", help="List all configured patches")
    parser.add_argument("--dump-site",    metavar="VA",        help="Hex-dump the area at this VA (hex)")
    parser.add_argument("--find-caves",   action="store_true", help="Find code caves (INT3 padding blocks)")
    parser.add_argument("--build-bytes",  action="store_true", help="Print mirror-x byte sequences")
    parser.add_argument("--patch-id",     metavar="ID",        help="Only operate on patch with this id")
    parser.add_argument("--exe",          default=EXE_PATH,    help="Path to exe")
    args = parser.parse_args()

    data, va_to_off, off_to_va = load_and_map(args.exe)

    active_patches = [p for p in PATCHES if p.get("enabled", False)]
    if args.patch_id:
        active_patches = [p for p in PATCHES if p["id"] == args.patch_id]

    # ----

    if args.list_patches:
        print("\n=== CONFIGURED PATCHES ===\n")
        for p in PATCHES:
            status = verify_patch(data, va_to_off, p)
            va_s = f"0x{p['va']:08X}" if p["va"] else "(not set)"
            print(f"  [{p['id']}]")
            print(f"    VA       : {va_s}")
            print(f"    Status   : {status}")
            print(f"    Enabled  : {p.get('enabled', False)}")
            print(f"    Desc     : {p['description'][:80]}")
            print()
        return

    if args.verify:
        print("\n=== PATCH STATUS ===\n")
        for p in PATCHES:
            status = verify_patch(data, va_to_off, p)
            print(f"  {p['id']:30s}  {status}")
        return

    if args.dump_site:
        target_va = int(args.dump_site, 0)
        off = va_to_off(target_va)
        if off is None:
            print(f"ERROR: cannot map VA {args.dump_site}")
            return
        print(f"\nDump of 0x{target_va:08X} ({64} bytes):")
        hex_dump(data, off, 64, off_to_va)
        return

    if args.find_caves:
        print("\n=== CODE CAVES (INT3/NOP padding blocks >= 16 bytes) ===\n")
        caves = find_code_cave(data, min_size=16)
        for off, size, fb in sorted(caves, key=lambda x: -x[1])[:20]:
            va = off_to_va(off)
            fill_name = "INT3" if fb == 0xCC else "NOP"
            print(f"  VA=0x{va:08X}  file=0x{off:08X}  size={size} bytes  fill={fill_name}(0x{fb:02X})")
        if not caves:
            print("  No caves found in .text — use --add-section to inject a new code section")
        print(f"\n  Total caves found: {len(caves)}")
        print("  Tip: if no caves, run --add-section to append a new .cave section to the PE")
        return

    if args.build_bytes:
        print("\n=== MIRROR-X BYTE SEQUENCES ===\n")
        print("  Integer eax (neg + add):")
        b = build_mirror_x_int_eax(1280)
        print(f"    Bytes (1280): {b.hex()}")
        b = build_mirror_x_int_eax(1024)
        print(f"    Bytes (1024): {b.hex()}")
        build_mirror_x_xmm0_float(1280.0)
        return

    if args.apply:
        if not active_patches:
            print("No enabled patches to apply.  Edit PATCHES list in this file first.")
            print("Run with --list-patches to see what is configured.")
            return
        print(f"\nApplying {len(active_patches)} patch(es) to {args.exe}")
        make_backup(args.exe)
        ok = True
        for p in active_patches:
            print(f"\n  Patch: {p['id']}")
            ok &= apply_patch(data, va_to_off, p)
        if ok:
            write_exe(data, args.exe)
            print("\nDone. Patches applied successfully.")
        else:
            print("\nERRORS occurred — exe NOT modified.")
        return

    if args.restore:
        if not active_patches:
            print("No enabled patches to restore.")
            return
        print(f"\nRestoring {len(active_patches)} patch(es) in {args.exe}")
        make_backup(args.exe)
        ok = True
        for p in active_patches:
            print(f"\n  Patch: {p['id']}")
            ok &= restore_patch(data, va_to_off, p)
        if ok:
            write_exe(data, args.exe)
            print("\nDone. Patches restored.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()

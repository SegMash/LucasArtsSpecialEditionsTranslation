"""
disasm_region.py - Hex dump + lightweight x86 instruction decoder for Monkey2.exe

This is NOT a full disassembler. It implements a length-only decoder (using standard
x86 opcode length tables) so it can print instruction boundaries and annotate
CALL/JMP targets, PUSH immediates, and MOV-with-immediate patterns — which are the
most useful for tracing how x,y coordinates flow through the rendering code.

Usage:
    python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00
    python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00 --len 256
    python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00 --follow-calls
    python scripts/reverse-engineering/disasm_region.py --offset 0x000DBE00 --len 128
    python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00 --find-xrefs
"""

import struct
import os
import argparse

EXE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "Monkey2.exe")
IMAGE_BASE = 0x00400000


# ---------------------------------------------------------------------------
# Minimal x86-32 instruction length decoder
# Based on the classic one-table decoder. Handles the common opcodes we care
# about.  Prefix bytes are handled.  Errors produce length=1 (safe fallback).
# ---------------------------------------------------------------------------

# Primary opcode table: value = instruction length (operand bytes), or special
# 0 = needs ModRM, -1 = two-byte escape (0F xx), -2 = prefix (doesn't count)
# Lengths here are the *bytes after the opcode byte* (NOT including opcode itself)

_MODRM_NEEDED = 0
_TWO_BYTE_ESC = -1
_PREFIX       = -2

# fmt: off
_OP1_LEN = [
    # 0x00-0x0F
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, 0, 0,  # 00-07
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, 0, _TWO_BYTE_ESC,  # 08-0F
    # 0x10-0x1F
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, 0, 0,
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, 0, 0,
    # 0x20-0x2F
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, _PREFIX, 0,
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, _PREFIX, 0,
    # 0x30-0x3F
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, _PREFIX, 0,
    _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, _MODRM_NEEDED, 1, 4, _PREFIX, 0,
    # 0x40-0x4F (INC/DEC reg32)
    0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,0,
    # 0x50-0x5F (PUSH/POP reg32)
    0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,0,
    # 0x60-0x6F
    0,0,_MODRM_NEEDED,_MODRM_NEEDED, _PREFIX,_PREFIX,_PREFIX,_PREFIX,
    4,_MODRM_NEEDED+4,1,_MODRM_NEEDED+1, 0,0,0,0,
    # 0x70-0x7F (Jcc short)
    1,1,1,1, 1,1,1,1, 1,1,1,1, 1,1,1,1,
    # 0x80-0x8F
    _MODRM_NEEDED+1,_MODRM_NEEDED+4,_MODRM_NEEDED+1,_MODRM_NEEDED+1,
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    # 0x90-0x9F
    0,0,0,0, 0,0,0,0, 0,0,4,0, 0,0,0,0,
    # 0xA0-0xAF
    4,4,4,4, 1,1,0,0, 1,4,0,0, 0,0,0,0,
    # 0xB0-0xBF (MOV reg, imm)
    1,1,1,1, 1,1,1,1, 4,4,4,4, 4,4,4,4,
    # 0xC0-0xCF
    _MODRM_NEEDED+1,_MODRM_NEEDED+1,2,0, 4,4,_MODRM_NEEDED+1,_MODRM_NEEDED+4,
    3,0,2,0, 0,1,0,0,
    # 0xD0-0xDF (shifts, FPU)
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    1,1,0,0,
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    _MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,_MODRM_NEEDED,
    # 0xE0-0xEF
    1,1,1,1, 1,4,1,4, 0,0,4,1, 0,0,0,0,
    # 0xF0-0xFF
    _PREFIX,0,_PREFIX,_PREFIX, 0,0,_MODRM_NEEDED,_MODRM_NEEDED,
    0,0,0,0, 0,0,_MODRM_NEEDED,_MODRM_NEEDED,
]
# fmt: on


def _modrm_extra_len(data, off):
    """Return the number of bytes consumed by a ModRM + SIB + displacement."""
    if off >= len(data):
        return 1
    modrm = data[off]
    mod = (modrm >> 6) & 3
    rm  = modrm & 7
    extra = 1  # for the modrm byte itself
    if mod == 3:
        return extra  # register operand, no displacement
    if mod == 0:
        if rm == 5:
            return extra + 4  # disp32 (absolute address)
        if rm == 4:
            extra += 1  # SIB
            sib = data[off + 1] if off + 1 < len(data) else 0
            if (sib & 7) == 5:  # SIB base==EBP, disp32
                return extra + 4
        return extra
    if mod == 1:
        if rm == 4:
            extra += 1  # SIB
        return extra + 1  # disp8
    if mod == 2:
        if rm == 4:
            extra += 1  # SIB
        return extra + 4  # disp32
    return extra


def insn_length(data, off):
    """Return the byte length of the x86-32 instruction at data[off].
    Returns 1 on error/unknown (safe fallback)."""
    if off >= len(data):
        return 1
    prefix_count = 0
    orig_off = off

    # Consume prefix bytes (up to 4)
    while prefix_count < 4 and off < len(data):
        b = data[off]
        if b in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3):
            prefix_count += 1
            off += 1
        else:
            break

    if off >= len(data):
        return 1

    op = data[off]

    # Two-byte escape 0F xx
    if op == 0x0F:
        if off + 1 >= len(data):
            return 1
        op2 = data[off + 1]
        # Most 0F xx instructions: add modRM
        if op2 in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
                   0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F,
                   0x38, 0x39, 0x3A,
                   0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47,
                   0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
                   0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57,
                   0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F,
                   0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67,
                   0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F,
                   0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77,
                   0xAE, 0xAF,
                   0xB0, 0xB1, 0xB3, 0xB6, 0xB7, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF,
                   0xC0, 0xC1,
                   0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7,
                   0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF,
                   0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7,
                   0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF,
                   0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
                   0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF):
            modrm_len = _modrm_extra_len(data, off + 2)
            if op2 in (0x70, 0x71, 0x72, 0x73, 0xAE, 0xC0, 0xC1, 0xC4, 0xC5, 0xC6):
                return (off - orig_off) + 2 + modrm_len + 1  # +imm8
            return (off - orig_off) + 2 + modrm_len
        if 0x80 <= op2 <= 0x8F:  # Jcc rel32
            return (off - orig_off) + 2 + 4
        if 0x90 <= op2 <= 0x9F:  # SETcc r/m8
            modrm_len = _modrm_extra_len(data, off + 2)
            return (off - orig_off) + 2 + modrm_len
        return (off - orig_off) + 2

    entry = _OP1_LEN[op] if op < 256 else 1

    if entry == _PREFIX:
        # already consumed above; count as 1 byte
        return (off - orig_off) + 1

    if entry == _TWO_BYTE_ESC:
        return (off - orig_off) + 2  # fallback

    if entry == _MODRM_NEEDED:
        modrm_len = _modrm_extra_len(data, off + 1)
        return (off - orig_off) + 1 + modrm_len

    # entry >= 0: instruction length = 1 (opcode) + entry (immediate/disp bytes)
    # But some entries encode modrm+imm: the positive value is *extra* imm bytes
    # after the ModRM.
    if entry > 0 and op in (0x80, 0x81, 0x82, 0x83,
                             0xC0, 0xC1, 0x69, 0x6B, 0xC6, 0xC7):
        modrm_len = _modrm_extra_len(data, off + 1)
        imm_size = entry & 0x0F
        return (off - orig_off) + 1 + modrm_len + imm_size

    return (off - orig_off) + 1 + (entry if entry > 0 else 0)


# ---------------------------------------------------------------------------
# Instruction annotation
# ---------------------------------------------------------------------------

def _decode_annotation(data, off, va):
    """Return a short human-readable annotation for certain important instructions."""
    if off >= len(data):
        return ""
    op = data[off]

    # CALL rel32
    if op == 0xE8 and off + 4 < len(data):
        rel = struct.unpack_from("<i", data, off + 1)[0]
        target = (va + 5 + rel) & 0xFFFFFFFF
        return f"CALL -> 0x{target:08X}"

    # JMP rel32
    if op == 0xE9 and off + 4 < len(data):
        rel = struct.unpack_from("<i", data, off + 1)[0]
        target = (va + 5 + rel) & 0xFFFFFFFF
        return f"JMP  -> 0x{target:08X}"

    # JMP short / Jcc short
    if op == 0xEB and off + 1 < len(data):
        rel = struct.unpack_from("<b", data, off + 1)[0]
        target = (va + 2 + rel) & 0xFFFFFFFF
        return f"JMPs -> 0x{target:08X}"
    if 0x70 <= op <= 0x7F and off + 1 < len(data):
        rel = struct.unpack_from("<b", data, off + 1)[0]
        target = (va + 2 + rel) & 0xFFFFFFFF
        cond = ["O","NO","B","NB","Z","NZ","NA","A","S","NS","P","NP","L","NL","NG","G"][op-0x70]
        return f"J{cond}   -> 0x{target:08X}"

    # MOV reg32, imm32  (B8+r)
    if 0xB8 <= op <= 0xBF and off + 4 < len(data):
        imm = struct.unpack_from("<I", data, off + 1)[0]
        reg = ["eax","ecx","edx","ebx","esp","ebp","esi","edi"][op - 0xB8]
        # Flag interesting immediates
        note = ""
        if IMAGE_BASE <= imm < IMAGE_BASE + 0x200000:
            note = "  <- code/data pointer"
        elif imm in (640, 800, 1024, 1280, 1920, 720, 768, 600):
            note = f"  <- screen dim ({imm})"
        elif 0x40000000 <= imm <= 0x45000000:  # likely float
            import struct as st
            fv = st.unpack("<f", st.pack("<I", imm))[0]
            if 100 < fv < 4000:
                note = f"  <- float {fv:.1f}"
        return f"MOV {reg}, 0x{imm:08X}{note}"

    # PUSH imm32
    if op == 0x68 and off + 4 < len(data):
        imm = struct.unpack_from("<I", data, off + 1)[0]
        note = ""
        if IMAGE_BASE <= imm < IMAGE_BASE + 0x200000:
            note = "  <- addr"
        elif imm in (640, 800, 1024, 1280, 1920):
            note = f"  <- screen_w={imm}"
        return f"PUSH 0x{imm:08X}{note}"

    # PUSH imm8
    if op == 0x6A and off + 1 < len(data):
        imm = struct.unpack_from("<b", data, off + 1)[0]
        return f"PUSH byte {imm}"

    # SUB esp, imm8 (83 EC xx) — stack frame
    if op == 0x83 and off + 1 < len(data) and data[off+1] == 0xEC:
        imm = data[off+2] if off+2 < len(data) else 0
        return f"SUB  esp, 0x{imm:02X}  <- stack frame {imm}b"

    # PUSH ebp / MOV ebp,esp  — function prologue
    if op == 0x55:
        return "PUSH ebp  <- prologue"
    if op == 0x8B and off+1 < len(data) and data[off+1] == 0xEC:
        return "MOV  ebp, esp  <- prologue"

    # RET
    if op in (0xC3, 0xC2):
        return "RET"

    # INT3 (padding)
    if op == 0xCC:
        return "INT3 (padding)"

    # MOV [esp+N], imm32  — 89 44 24 NN / C7 44 24 NN
    if op == 0xC7 and off+1 < len(data) and data[off+1] == 0x44 and off+2 < len(data) and data[off+2] == 0x24:
        disp = data[off+3] if off+3 < len(data) else 0
        imm = struct.unpack_from("<I", data, off+4)[0] if off+4 < len(data) else 0
        return f"MOV  [esp+0x{disp:02X}], 0x{imm:08X}"

    # movss xmm, [mem]  (F3 0F 10)  / movsd xmm, [mem]  (F2 0F 10)
    if op in (0xF2, 0xF3) and off+2 < len(data) and data[off+1] == 0x0F:
        op2 = data[off+2]
        kind = "movss" if op == 0xF3 else "movsd"
        if op2 == 0x10: return f"{kind} xmm, ..."
        if op2 == 0x11: return f"{kind} [...], xmm"
        if op2 == 0x5C: return f"{'subss' if op==0xF3 else 'subsd'} xmm, ..."
        if op2 == 0x58: return f"{'addss' if op==0xF3 else 'addsd'} xmm, ..."
        if op2 == 0x59: return f"{'mulss' if op==0xF3 else 'mulsd'} xmm, ..."
        if op2 == 0x5E: return f"{'divss' if op==0xF3 else 'divsd'} xmm, ..."
        if op2 == 0x2A: return f"cvtsi2{'ss' if op==0xF3 else 'sd'} xmm, ..."
        if op2 == 0x2C: return f"cvtt{'ss' if op==0xF3 else 'sd'}2si ..."
        if op2 == 0x57: return f"xor{'ps' if op==0xF3 else 'pd'} xmm, ..."

    return ""


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

    def off_to_va(off):
        for _, va, ro, rs in sections:
            if ro <= off < ro + rs:
                return IMAGE_BASE + va + (off - ro)
        return None

    def va_to_off(va):
        rva = va - IMAGE_BASE
        for _, vaddr, ro, rs in sections:
            if vaddr <= rva < vaddr + rs:
                return ro + (rva - vaddr)
        return None

    return data, off_to_va, va_to_off


# ---------------------------------------------------------------------------
# Main disassembly loop
# ---------------------------------------------------------------------------

def disassemble(data, start_off, length, off_to_va, follow_calls=False, visited=None):
    if visited is None:
        visited = set()

    off = start_off
    end = start_off + length

    while off < end and off < len(data):
        va = off_to_va(off)
        va_str = f"0x{va:08X}" if va else "???????  "

        ilen = insn_length(data, off)
        ilen = max(1, min(ilen, 15))  # safety clamp

        raw_bytes = bytes(data[off:off+ilen])
        hex_part = " ".join(f"{b:02X}" for b in raw_bytes)
        annotation = _decode_annotation(data, off, va) if va else ""

        print(f"  {va_str}: {hex_part:<30}  {annotation}")

        # Follow CALL if requested
        if follow_calls and data[off] == 0xE8 and va:
            rel = struct.unpack_from("<i", data, off + 1)[0]
            target_va = (va + 5 + rel) & 0xFFFFFFFF
            if IMAGE_BASE <= target_va < IMAGE_BASE + 0x200000 and target_va not in visited:
                # get file offset
                from_data, fo2, vo2 = off_to_va, None, None
                # inline resolve
                rva = target_va - IMAGE_BASE
                for _, vaddr, ro, rs in []:
                    pass
                # We can't easily resolve here without sections list — skip

        off += ilen

        # Stop at RET
        if data[off - ilen] in (0xC3, 0xC2, 0xCB):
            if off < end:
                print(f"  {'':9}  --- end of function ---")
                # Keep going if more bytes requested


def main():
    parser = argparse.ArgumentParser(description="Dump and annotate x86 code from Monkey2.exe")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--va", help="Virtual address to start (hex, e.g. 0x004DCA00)")
    group.add_argument("--offset", help="File offset to start (hex, e.g. 0x000DBE00)")
    parser.add_argument("--len", type=lambda x: int(x,0), default=256, help="Bytes to dump (default 256)")
    parser.add_argument("--follow-calls", action="store_true", help="(Future) Recursively follow CALL targets")
    parser.add_argument("--find-xrefs", action="store_true", help="Find code that references this VA as an operand")
    parser.add_argument("--exe", default=EXE_PATH, help="Path to exe")
    args = parser.parse_args()

    data, off_to_va, va_to_off = load_and_map(args.exe)

    if args.va:
        start_va = int(args.va, 0)
        start_off = va_to_off(start_va)
        if start_off is None:
            print(f"ERROR: VA {args.va} not found in any section")
            return
    else:
        start_off = int(args.offset, 0)
        start_va = off_to_va(start_off)

    print(f"\nDisassembly of Monkey2.exe")
    print(f"  Start VA  : 0x{(start_va or 0):08X}")
    print(f"  File off  : 0x{start_off:08X}")
    print(f"  Length    : {args.len} bytes")

    if args.find_xrefs:
        # Search for 4-byte occurrences of start_va in .text
        if start_va is None:
            print("ERROR: need VA for xref search")
            return
        target_bytes = struct.pack("<I", start_va)
        print(f"\nXrefs to 0x{start_va:08X}:")
        idx = 0x400
        text_end = 0x400 + 1105920
        while True:
            idx = data.find(target_bytes, idx, text_end)
            if idx < 0:
                break
            ref_va = off_to_va(idx)
            ctx = bytes(data[max(0,idx-4):idx+8])
            print(f"  file=0x{idx:08X}  VA=0x{ref_va:08X}  ctx={ctx.hex()}")
            idx += 4
        return

    print(f"\n  {'ADDRESS':<12}  {'BYTES':<30}  ANNOTATION")
    print(f"  {'-'*12}  {'-'*30}  {'-'*40}")
    disassemble(data, start_off, args.len, off_to_va)


if __name__ == "__main__":
    main()

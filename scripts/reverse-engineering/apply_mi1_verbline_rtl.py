#!/usr/bin/env python3
"""Reverse the MI1 SE sentence-line component order for Hebrew RTL.

The SE sentence line is built by FUN_0x0047C390 as four separate 64-byte
component strings (screen order = the order they appear left-to-right):

    screen slot 0  ->  0x56FB30   (verb,  id 0x6b)
    screen slot 1  ->  0x56FB70   (obj1,  id 0x6c)
    screen slot 2  ->  0x56FBF0   (prep,  id 0x6e)
    screen slot 3  ->  0x56FBB0   (obj2,  id 0x6d)

FUN_0x0048A5F0 copies all four (one 256-byte `rep movsd`) into UI text widgets
and shows only the first N (N depends on verb type: 1-object shows 2, give-type
shows 4). Empty components always trail in screen order.

For Hebrew RTL the visible components must be reversed:
    English  [verb][obj1][prep][obj2]  ->  [obj2][prep][obj1][verb]
    English  [verb][obj1]              ->  [obj1][verb]

Letters inside each component are already reversed at inject time, so we only
reverse the *component* order. We hook the builder's final tail-jump
(0x0047C3DB: `jmp 0x497CE0`) into a code cave that:

  1. calls 0x497CE0 to finish building the 4th component,
  2. counts M = leading non-empty components (in screen order),
  3. reverses the first M 64-byte blocks in place,
  4. returns to the builder's caller.

Because the builder rewrites the buffers in original order on every rebuild
before the cave runs, the reversal is idempotent (no flip-flop).

Requires: pefile, keystone-engine.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

import pefile

try:
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
except ImportError:
    print("[-] keystone-engine is required:  pip install keystone-engine", file=sys.stderr)
    raise

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM
except ImportError:
    Cs = None
    CS_ARCH_X86 = None
    CS_MODE_32 = None
    X86_OP_IMM = None
    X86_OP_MEM = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INPUT = os.path.join(BASE_DIR, "MISE_gog.exe")
# Stage 1 of 2: write a local intermediate. apply_mi1_merge_to_object.py reads
# this and performs the single write into the installed game folder (which is
# under aggressive antivirus locking).
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "MISE.tmp.exe")
IMAGE_BASE = 0x00400000

# component buffers in screen (left-to-right) order: verb, obj1, prep, obj2
SCREEN_BUFS = (0x0056FB30, 0x0056FB70, 0x0056FBF0, 0x0056FBB0)
PREP_BUF = 0x0056FBF0             # screen slot 2 (preposition)
OBJ2_BUF = 0x0056FBB0             # screen slot 3 (second object)
COMPONENT_INSERTER = 0x00497CE0   # word/verb inserter (the builder's tail target)

# Single-character proclitic prepositions to merge onto the following object
# with no space: lamed (to, 234) and bet (in, 229). Two-char preps (with/on)
# are left as separate tokens.
MERGE_PREPS = (234, 229)

# hijack: builder's final tail-jump `jmp 0x497CE0`
HOOK_SITE = 0x0047C3DB
HOOK_ORIG = bytes.fromhex("e900b90100")   # jmp 0x497CE0

OFF_CODE = 0x000
OFF_TABLE = 0x100                 # 4 dwords: SCREEN_BUFS


def _asm(ks: Ks, asm: str, va: int) -> bytes:
    lines = [ln.split(";", 1)[0].rstrip() for ln in asm.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    encoding, _ = ks.asm("\n".join(lines), va)
    return bytes(encoding)


def find_code_cave(pe: pefile.PE, min_zeros: int = 160):
    for section in pe.sections:
        if b".text" in section.Name:
            data = section.get_data()
            idx = data.find(b"\x00" * min_zeros)
            if idx != -1:
                run = len(data[idx:]) - len(data[idx:].lstrip(b"\x00"))
                return section.PointerToRawData + idx, IMAGE_BASE + section.VirtualAddress + idx, run
    return None, None, 0


def _get_text_section(pe: pefile.PE) -> pefile.SectionStructure:
    for section in pe.sections:
        if b".text" in section.Name:
            return section
    raise RuntimeError("No .text section found")


def _decode_e9_target(src_va: int, instr5: bytes) -> int:
    if len(instr5) != 5 or instr5[0] != 0xE9:
        raise ValueError("Expected E9 rel32 jmp")
    rel = struct.unpack("<i", instr5[1:5])[0]
    return src_va + 5 + rel


def _find_builder_tail_jmp(pe: pefile.PE, data: bytearray, window: int = 0x120) -> tuple[int, bytes, tuple[int, int, int, int]]:
    section = _get_text_section(pe)
    text_off = section.PointerToRawData
    text_size = section.SizeOfRawData
    text_va = IMAGE_BASE + section.VirtualAddress
    text_data = bytes(data[text_off:text_off + text_size])

    if Cs is not None:
        md = Cs(CS_ARCH_X86, CS_MODE_32)
        md.detail = True
        insns = list(md.disasm(text_data, text_va))
        ids = {0x6B, 0x6C, 0x6D, 0x6E}

        def mov_abs_imm(insn):
            if insn.mnemonic != "mov" or len(insn.operands) != 2:
                return None
            op0, op1 = insn.operands
            if op0.type != X86_OP_MEM or op1.type != X86_OP_IMM:
                return None
            if op0.mem.base != 0 or op0.mem.index != 0:
                return None
            return op0.mem.disp & 0xFFFFFFFF, op1.imm & 0xFFFFFFFF

        def near_id(insn_idx: int):
            for k in range(1, 7):
                j = insn_idx - k
                if j < 0:
                    break
                insn = insns[j]
                if len(insn.operands) != 1 and len(insn.operands) != 2:
                    continue
                for op in insn.operands:
                    if op.type == X86_OP_IMM and (op.imm & 0xFFFFFFFF) in ids:
                        return op.imm & 0xFFFFFFFF
            return None

        for i, insn in enumerate(insns):
            m = mov_abs_imm(insn)
            if m is None:
                continue
            dest, src = m
            seq = [(i, src, near_id(i))]
            j = i + 1
            while j < len(insns) and len(seq) < 4 and insns[j].address - insn.address <= 0x120:
                m2 = mov_abs_imm(insns[j])
                if m2 is not None and m2[0] == dest:
                    seq.append((j, m2[1], near_id(j)))
                j += 1
            if len(seq) != 4:
                continue
            jmps = []
            for k in range(seq[-1][0] + 1, min(seq[-1][0] + 12, len(insns))):
                if insns[k].mnemonic == "jmp" and insns[k].op_str.startswith("0x"):
                    jmps.append(insns[k])
            if not jmps:
                continue
            hook_site_va = jmps[0].address
            hook_off = hook_site_va - text_va
            if hook_off < 0 or hook_off + 5 > len(text_data):
                continue
            orig5 = text_data[hook_off:hook_off + 5]
            if len(orig5) != 5 or orig5[0] != 0xE9:
                continue

            by_id: dict[int, int] = {}
            for _, buf, ident in seq:
                if ident is not None:
                    by_id[ident] = buf
            if ids.issubset(set(by_id.keys())):
                screen_bufs = (by_id[0x6B], by_id[0x6C], by_id[0x6E], by_id[0x6D])
            else:
                buf1, buf2, buf3, buf4 = [x[1] for x in seq]
                screen_bufs = (buf1, buf2, buf4, buf3)
            return hook_site_va, orig5, screen_bufs

    prefix = b"\xB8\x6B\x00\x00\x00\xC7\x05"

    start = 0
    while True:
        pos = text_data.find(prefix, start)
        if pos == -1:
            break
        ok = True
        checks = [
            (0, b"\xB8\x6B\x00\x00\x00"),
            (5, b"\xC7\x05"),
            (15, b"\xE8"),
            (20, b"\xB8\x6C\x00\x00\x00"),
            (25, b"\xC7\x05"),
            (35, b"\xE8"),
            (40, b"\xB8\x6D\x00\x00\x00"),
            (45, b"\xC7\x05"),
            (55, b"\xE8"),
            (60, b"\xC7\x05"),
            (70, b"\xB8\x6E\x00\x00\x00"),
            (75, b"\xE9"),
        ]
        for off, sig in checks:
            if text_data[pos + off:pos + off + len(sig)] != sig:
                ok = False
                break
        if ok:
            buf1 = struct.unpack("<I", text_data[pos + 11:pos + 15])[0]
            buf2 = struct.unpack("<I", text_data[pos + 31:pos + 35])[0]
            buf3 = struct.unpack("<I", text_data[pos + 51:pos + 55])[0]
            buf4 = struct.unpack("<I", text_data[pos + 66:pos + 70])[0]
            screen_bufs = (buf1, buf2, buf4, buf3)
            hook_site_va = text_va + pos + 75
            orig5 = text_data[pos + 75:pos + 80]
            return hook_site_va, orig5, screen_bufs
        start = pos + 1

    best_pos = None
    needles = [struct.pack("<I", va) for va in SCREEN_BUFS]
    for i in range(0, len(text_data) - 5):
        if text_data[i] != 0xE9:
            continue
        lo = max(0, i - window)
        hi = min(len(text_data), i + window)
        win = text_data[lo:hi]
        if all(win.find(nd) != -1 for nd in needles):
            best_pos = i
    if best_pos is None:
        raise RuntimeError("Could not locate builder tail jump in .text")

    hook_site_va = text_va + best_pos
    orig5 = text_data[best_pos:best_pos + 5]
    return hook_site_va, orig5, SCREEN_BUFS


def build_cave(cave_va: int, table_va: int, component_inserter_va: int) -> bytes:
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    asm = f"""
        call {component_inserter_va:#x}        ; finish building 4th component
        pushad
        pushfd

        ; NOTE: no prep/obj2 merge here. The buffers hold ENGLISH at this
        ; point (localization is a per-widget whole-string lookup done later
        ; at draw), so merging buffers would break translation. We only
        ; reverse component order here; the lamed/bet merge must be done on
        ; the Hebrew side at the localizer/draw hook (TODO).

        ; ebx = M = leading non-empty component count (screen order)
        xor ebx, ebx
        mov eax, dword ptr [{table_va + 0:#x}]
        cmp byte ptr [eax], 0
        je mdone
        inc ebx
        mov eax, dword ptr [{table_va + 4:#x}]
        cmp byte ptr [eax], 0
        je mdone
        inc ebx
        mov eax, dword ptr [{table_va + 8:#x}]
        cmp byte ptr [eax], 0
        je mdone
        inc ebx
        mov eax, dword ptr [{table_va + 12:#x}]
        cmp byte ptr [eax], 0
        je mdone
        inc ebx
    mdone:

        ; reverse first M 64-byte blocks: i=0, j=M-1
        xor ecx, ecx
        lea edx, [ebx - 1]
    sloop:
        cmp ecx, edx
        jge sdone
        mov esi, dword ptr [ecx*4 + {table_va:#x}]
        mov edi, dword ptr [edx*4 + {table_va:#x}]
        push ecx
        push edx
        mov ecx, 16
    b64:
        mov eax, dword ptr [esi]
        mov ebp, dword ptr [edi]
        mov dword ptr [edi], eax
        mov dword ptr [esi], ebp
        add esi, 4
        add edi, 4
        dec ecx
        jnz b64
        pop edx
        pop ecx
        inc ecx
        dec edx
        jmp sloop
    sdone:
        popfd
        popad
        ret
    """
    return _asm(ks, asm, cave_va)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--hook-site", type=lambda s: int(s, 0), default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"[-] Input not found: {args.input}", file=sys.stderr)
        return 1

    pe = pefile.PE(args.input)
    ep_rva = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    ep_section = None
    for s in pe.sections:
        start = s.VirtualAddress
        end = start + max(s.Misc_VirtualSize, s.SizeOfRawData)
        if start <= ep_rva < end:
            ep_section = s.Name.rstrip(b"\x00")
            break
    if ep_section is not None and b".text" not in ep_section:
        print(f"[-] Entry point is in {ep_section.decode(errors='replace')}, not .text.", file=sys.stderr)
        print("[-] This executable looks SteamStub-protected (packed/encrypted).", file=sys.stderr)
        print("[-] Unpack it first (e.g. with Steamless), then run this script on the unpacked exe.",
              file=sys.stderr)
        return 1
    with open(args.input, "rb") as f:
        data = bytearray(f.read())

    def off(va: int) -> int:
        return pe.get_offset_from_rva(va - IMAGE_BASE)

    auto_hook_site_va = None
    auto_hook_orig5 = None
    screen_bufs = SCREEN_BUFS
    try:
        auto_hook_site_va, auto_hook_orig5, screen_bufs = _find_builder_tail_jmp(pe, data)
    except Exception:  # noqa: BLE001
        pass

    if args.hook_site is None:
        if auto_hook_site_va is None or auto_hook_orig5 is None:
            print("[-] Could not auto-locate builder tail jump.", file=sys.stderr)
            print("[i] If needed, retry with --hook-site 0x........", file=sys.stderr)
            return 1
        hook_site_va, hook_orig5 = auto_hook_site_va, auto_hook_orig5
    else:
        hook_site_va = args.hook_site
        site = off(hook_site_va)
        hook_orig5 = bytes(data[site:site + 5])

    if hook_orig5[0] != 0xE9:
        print(f"[-] {hook_site_va:#010x} is not a JMP rel32 (E9): {hook_orig5.hex()}", file=sys.stderr)
        return 1

    component_inserter_va = _decode_e9_target(hook_site_va, hook_orig5)
    site = off(hook_site_va)
    actual = bytes(data[site:site + 5])
    if actual != hook_orig5:
        print(f"[-] {hook_site_va:#010x} changed while reading: {actual.hex()} (expected {hook_orig5.hex()}).",
              file=sys.stderr)
        return 1
    print(f"[i] Builder hook site: {hook_site_va:#010x} (vanilla target {component_inserter_va:#010x})")
    print(f"[i] Screen buffers: {[hex(x) for x in screen_bufs]}")

    cave_off, cave_va, cave_run = find_code_cave(pe)
    if cave_va is None:
        print("[-] No code cave found.", file=sys.stderr)
        return 1
    print(f"[+] Code cave: VA={cave_va:#010x} file={cave_off:#010x} run={cave_run}")

    table_va = cave_va + OFF_TABLE
    cave = build_cave(cave_va, table_va, component_inserter_va)
    print(f"[+] cave: {len(cave)} bytes @ {cave_va:#010x}")
    if len(cave) > OFF_TABLE:
        print("[-] cave code overflows the address table.", file=sys.stderr)
        return 1
    if OFF_TABLE + 16 > cave_run:
        print("[-] table exceeds cave.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[i] dry-run: no file written.")
        return 0

    data[cave_off + OFF_CODE:cave_off + OFF_CODE + len(cave)] = cave
    data[cave_off + OFF_TABLE:cave_off + OFF_TABLE + 16] = struct.pack("<4I", *screen_bufs)

    rel = cave_va - (hook_site_va + 5)
    data[site:site + 5] = b"\xE9" + struct.pack("<i", rel)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[+] Patched exe written: {args.output}")
    print("[+] Sentence line reverses its visible components for Hebrew RTL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

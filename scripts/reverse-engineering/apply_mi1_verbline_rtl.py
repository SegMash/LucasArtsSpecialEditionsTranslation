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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INPUT = os.path.join(BASE_DIR, "MISE.exe")
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


def build_cave(cave_va: int, table_va: int) -> bytes:
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    asm = f"""
        call {COMPONENT_INSERTER:#x}        ; finish building 4th component
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"[-] Input not found: {args.input}", file=sys.stderr)
        return 1

    pe = pefile.PE(args.input)
    with open(args.input, "rb") as f:
        data = bytearray(f.read())

    def off(va: int) -> int:
        return pe.get_offset_from_rva(va - IMAGE_BASE)

    site = off(HOOK_SITE)
    actual = bytes(data[site:site + len(HOOK_ORIG)])
    if actual != HOOK_ORIG:
        print(f"[-] {HOOK_SITE:#010x} has {actual.hex()} (expected {HOOK_ORIG.hex()}). "
              "Run on a clean MISE.exe.", file=sys.stderr)
        return 1

    cave_off, cave_va, cave_run = find_code_cave(pe)
    if cave_va is None:
        print("[-] No code cave found.", file=sys.stderr)
        return 1
    print(f"[+] Code cave: VA={cave_va:#010x} file={cave_off:#010x} run={cave_run}")

    table_va = cave_va + OFF_TABLE
    cave = build_cave(cave_va, table_va)
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
    data[cave_off + OFF_TABLE:cave_off + OFF_TABLE + 16] = struct.pack("<4I", *SCREEN_BUFS)

    rel = cave_va - (HOOK_SITE + 5)
    data[site:site + 5] = b"\xE9" + struct.pack("<i", rel)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[+] Patched exe written: {args.output}")
    print("[+] Sentence line reverses its visible components for Hebrew RTL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

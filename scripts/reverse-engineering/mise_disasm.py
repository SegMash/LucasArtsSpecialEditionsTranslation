"""
mise_disasm.py - Capstone-backed analysis helper for MISE.exe (Monkey Island 1 SE).

Features:
  - Disassemble a region by VA (linear sweep).
  - Follow/annotate CALL and JMP targets.
  - Find code xrefs (absolute 4-byte operand references) to a VA.
  - Search for ASCII/relative-referenced strings and their code xrefs.
  - Find code caves (runs of 0x00 / 0xCC) in the .text section.

Usage:
  python scripts/reverse-engineering/mise_disasm.py disasm --va 0x0048A5F0 --len 400
  python scripts/reverse-engineering/mise_disasm.py xrefs  --va 0x0048A5F0
  python scripts/reverse-engineering/mise_disasm.py strings --filter with,to,use
  python scripts/reverse-engineering/mise_disasm.py strxref --text "with"
  python scripts/reverse-engineering/mise_disasm.py caves  --min 64
"""

from __future__ import annotations

import argparse
import os
import struct

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_EXE = os.path.join(BASE_DIR, "MISE.exe")


class Image:
    def __init__(self, path: str):
        self.path = path
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        with open(path, "rb") as f:
            self.raw = bytearray(f.read())
        self.md = Cs(CS_ARCH_X86, CS_MODE_32)
        self.md.detail = True

    def va_to_off(self, va: int):
        rva = va - self.base
        for s in self.pe.sections:
            if s.VirtualAddress <= rva < s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData):
                delta = rva - s.VirtualAddress
                if delta < s.SizeOfRawData:
                    return s.PointerToRawData + delta
        return None

    def off_to_va(self, off: int):
        for s in self.pe.sections:
            if s.PointerToRawData <= off < s.PointerToRawData + s.SizeOfRawData:
                return self.base + s.VirtualAddress + (off - s.PointerToRawData)
        return None

    def read_va(self, va: int, n: int) -> bytes:
        off = self.va_to_off(va)
        if off is None:
            return b""
        return bytes(self.raw[off:off + n])

    def text_section(self):
        for s in self.pe.sections:
            if b".text" in s.Name:
                return s
        return self.pe.sections[0]


def cmd_disasm(img: Image, args):
    va = int(args.va, 0)
    length = args.len
    code = img.read_va(va, length)
    print(f"\nDisassembly of {os.path.basename(img.path)} @ 0x{va:08X} ({length} bytes)\n")
    for insn in img.md.disasm(code, va):
        hexb = " ".join(f"{b:02X}" for b in insn.bytes)
        note = ""
        # annotate absolute memory operands / call-jmp targets
        if insn.mnemonic in ("call", "jmp") and insn.op_str.startswith("0x"):
            note = ""
        print(f"  0x{insn.address:08X}: {hexb:<24}  {insn.mnemonic} {insn.op_str}{note}")


def cmd_xrefs(img: Image, args):
    va = int(args.va, 0)
    target = struct.pack("<I", va)
    text = img.text_section()
    start = text.PointerToRawData
    end = start + text.SizeOfRawData
    print(f"\nAbsolute xrefs to 0x{va:08X} within .text:")
    idx = start
    hits = 0
    while True:
        idx = img.raw.find(target, idx, end)
        if idx < 0:
            break
        ref_va = img.off_to_va(idx)
        ctx = bytes(img.raw[max(0, idx - 6):idx + 6])
        print(f"  file=0x{idx:08X}  VA=0x{ref_va:08X}  ctx={ctx.hex()}")
        hits += 1
        idx += 1
    # also check for relative CALL/JMP (E8/E9) that land on va
    print(f"\nRelative CALL/JMP (E8/E9) that target 0x{va:08X}:")
    i = start
    while i < end - 5:
        op = img.raw[i]
        if op in (0xE8, 0xE9):
            rel = struct.unpack_from("<i", img.raw, i + 1)[0]
            src_va = img.off_to_va(i)
            if src_va is not None and (src_va + 5 + rel) & 0xFFFFFFFF == va:
                kind = "CALL" if op == 0xE8 else "JMP"
                print(f"  VA=0x{src_va:08X}  {kind} -> 0x{va:08X}")
                hits += 1
        i += 1
    if hits == 0:
        print("  (none)")


def _iter_strings(img: Image, min_len=4):
    """Yield (va, text) for printable ASCII runs in read-only data sections."""
    for s in img.pe.sections:
        name = s.Name.rstrip(b"\x00")
        if not (b".rdata" in name or b".data" in name or b".text" in name):
            continue
        data = img.raw[s.PointerToRawData:s.PointerToRawData + s.SizeOfRawData]
        i = 0
        n = len(data)
        while i < n:
            j = i
            while j < n and 0x20 <= data[j] < 0x7F:
                j += 1
            if j - i >= min_len and j < n and data[j] == 0:
                va = img.base + s.VirtualAddress + i
                yield va, data[i:j].decode("ascii", "replace")
            i = max(j + 1, i + 1)


def cmd_strings(img: Image, args):
    filters = [f.lower() for f in args.filter.split(",")] if args.filter else []
    print("\nStrings:")
    for va, text in _iter_strings(img, args.min):
        if filters and not any(f in text.lower() for f in filters):
            continue
        print(f"  0x{va:08X}: {text!r}")


def cmd_strxref(img: Image, args):
    """Find a string by exact/substring text, then list absolute xrefs to it."""
    needle = args.text.lower()
    matches = [(va, t) for va, t in _iter_strings(img, 2) if needle in t.lower()]
    for va, t in matches:
        print(f"\nString 0x{va:08X}: {t!r}")
        target = struct.pack("<I", va)
        text = img.text_section()
        start = text.PointerToRawData
        end = start + text.SizeOfRawData
        idx = start
        found = False
        while True:
            idx = img.raw.find(target, idx, end)
            if idx < 0:
                break
            ref_va = img.off_to_va(idx)
            ctx = bytes(img.raw[max(0, idx - 3):idx + 5])
            print(f"    xref VA=0x{ref_va:08X}  ctx={ctx.hex()}")
            found = True
            idx += 1
        if not found:
            print("    (no absolute xref found)")


def cmd_caves(img: Image, args):
    text = img.text_section()
    data = img.raw[text.PointerToRawData:text.PointerToRawData + text.SizeOfRawData]
    print(f"\nCode caves in .text (>= {args.min} bytes of 0x00/0xCC):")
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b in (0x00, 0xCC):
            j = i
            while j < n and data[j] == b:
                j += 1
            if j - i >= args.min:
                off = text.PointerToRawData + i
                va = img.base + text.VirtualAddress + i
                print(f"  VA=0x{va:08X}  file=0x{off:08X}  len={j - i}  fill=0x{b:02X}")
            i = j
        else:
            i += 1


def main():
    p = argparse.ArgumentParser(description="Capstone analysis helper for MISE.exe")
    p.add_argument("--exe", default=DEFAULT_EXE)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("disasm")
    d.add_argument("--va", required=True)
    d.add_argument("--len", type=lambda x: int(x, 0), default=256)

    x = sub.add_parser("xrefs")
    x.add_argument("--va", required=True)

    s = sub.add_parser("strings")
    s.add_argument("--filter", default="")
    s.add_argument("--min", type=int, default=4)

    sx = sub.add_parser("strxref")
    sx.add_argument("--text", required=True)

    c = sub.add_parser("caves")
    c.add_argument("--min", type=int, default=64)

    args = p.parse_args()
    img = Image(args.exe)

    {
        "disasm": cmd_disasm,
        "xrefs": cmd_xrefs,
        "strings": cmd_strings,
        "strxref": cmd_strxref,
        "caves": cmd_caves,
    }[args.cmd](img, args)


if __name__ == "__main__":
    main()

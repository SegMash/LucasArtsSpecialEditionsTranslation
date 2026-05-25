"""
pe_analysis.py - Full PE structure analysis for Monkey2.exe
Dumps: sections, imports, exports, key string locations, segment map.

Usage:
    python scripts/reverse-engineering/pe_analysis.py
    python scripts/reverse-engineering/pe_analysis.py --strings
    python scripts/reverse-engineering/pe_analysis.py --strings --filter font,draw,text
"""

import struct
import sys
import os
import argparse

EXE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "Monkey2.exe")
IMAGE_BASE = 0x00400000


def load_exe(path=EXE_PATH):
    with open(path, "rb") as f:
        return bytearray(f.read())


class PEParser:
    def __init__(self, data):
        self.data = data
        self._parse_headers()

    def _parse_headers(self):
        d = self.data
        assert d[:2] == b"MZ", "Not a valid MZ executable"

        self.pe_offset = struct.unpack_from("<I", d, 0x3C)[0]
        assert d[self.pe_offset : self.pe_offset + 4] == b"PE\x00\x00"

        self.machine = struct.unpack_from("<H", d, self.pe_offset + 4)[0]
        self.num_sections = struct.unpack_from("<H", d, self.pe_offset + 6)[0]
        self.opt_hdr_size = struct.unpack_from("<H", d, self.pe_offset + 20)[0]
        self.characteristics = struct.unpack_from("<H", d, self.pe_offset + 22)[0]

        opt = self.pe_offset + 24
        self.opt_magic = struct.unpack_from("<H", d, opt)[0]
        self.entry_point_rva = struct.unpack_from("<I", d, opt + 16)[0]
        self.image_base = struct.unpack_from("<I", d, opt + 28)[0]
        self.image_size = struct.unpack_from("<I", d, opt + 56)[0]

        self.sections = self._parse_sections()

        # Data directories
        dd_off = opt + 96
        self.import_dir_rva = struct.unpack_from("<I", d, dd_off + 8)[0]
        self.import_dir_size = struct.unpack_from("<I", d, dd_off + 12)[0]

    def _parse_sections(self):
        sections = []
        base = self.pe_offset + 24 + self.opt_hdr_size
        for i in range(self.num_sections):
            s = base + i * 40
            name = self.data[s : s + 8].rstrip(b"\x00").decode("ascii", errors="replace")
            vsize = struct.unpack_from("<I", self.data, s + 8)[0]
            vaddr = struct.unpack_from("<I", self.data, s + 12)[0]
            raw_size = struct.unpack_from("<I", self.data, s + 16)[0]
            raw_off = struct.unpack_from("<I", self.data, s + 20)[0]
            chars = struct.unpack_from("<I", self.data, s + 36)[0]
            sections.append(
                dict(name=name, vaddr=vaddr, vsize=vsize, raw_off=raw_off, raw_size=raw_size, chars=chars)
            )
        return sections

    def rva_to_offset(self, rva):
        for sec in self.sections:
            if sec["vaddr"] <= rva < sec["vaddr"] + sec["raw_size"]:
                return sec["raw_off"] + (rva - sec["vaddr"])
        return None

    def offset_to_rva(self, off):
        for sec in self.sections:
            if sec["raw_off"] <= off < sec["raw_off"] + sec["raw_size"]:
                return sec["vaddr"] + (off - sec["raw_off"])
        return None

    def offset_to_va(self, off):
        rva = self.offset_to_rva(off)
        return self.image_base + rva if rva is not None else None

    def va_to_offset(self, va):
        return self.rva_to_offset(va - self.image_base)

    def parse_imports(self):
        imports = {}
        if not self.import_dir_rva:
            return imports
        off = self.rva_to_offset(self.import_dir_rva)
        if off is None:
            return imports
        idx = 0
        while True:
            iid = off + idx * 20
            orig_thunk_rva = struct.unpack_from("<I", self.data, iid)[0]
            name_rva = struct.unpack_from("<I", self.data, iid + 12)[0]
            first_thunk_rva = struct.unpack_from("<I", self.data, iid + 16)[0]
            if orig_thunk_rva == 0 and name_rva == 0:
                break
            name_off = self.rva_to_offset(name_rva)
            dll = self.data[name_off : name_off + 64].split(b"\x00")[0].decode("ascii", errors="replace")
            imports[dll] = []
            thunk_off = self.rva_to_offset(orig_thunk_rva or first_thunk_rva)
            iat_off = self.rva_to_offset(first_thunk_rva)
            if thunk_off:
                fi = 0
                while fi < 500:
                    fn_rva = struct.unpack_from("<I", self.data, thunk_off + fi * 4)[0]
                    iat_va = self.image_base + first_thunk_rva + fi * 4
                    if fn_rva == 0:
                        break
                    if fn_rva & 0x80000000:
                        imports[dll].append(dict(name=f"#{fn_rva & 0x7FFFFFFF}", ordinal=True, iat_va=iat_va))
                    else:
                        fn_off = self.rva_to_offset(fn_rva + 2)
                        if fn_off:
                            fn_name = self.data[fn_off : fn_off + 128].split(b"\x00")[0].decode("ascii", errors="replace")
                            imports[dll].append(dict(name=fn_name, ordinal=False, iat_va=iat_va))
                    fi += 1
            idx += 1
        return imports

    def extract_strings(self, min_len=6, section_name=None):
        """Extract printable ASCII strings from specified section or all sections."""
        strings = []
        for sec in self.sections:
            if section_name and sec["name"] != section_name:
                continue
            start = sec["raw_off"]
            end = start + sec["raw_size"]
            chunk = self.data[start:end]
            cur = b""
            cur_start = 0
            for i, b in enumerate(chunk):
                if 32 <= b <= 126:
                    if not cur:
                        cur_start = i
                    cur += bytes([b])
                else:
                    if len(cur) >= min_len:
                        file_off = start + cur_start
                        va = self.offset_to_va(file_off)
                        strings.append(dict(off=file_off, va=va, text=cur.decode("ascii")))
                    cur = b""
            if len(cur) >= min_len:
                file_off = start + cur_start
                va = self.offset_to_va(file_off)
                strings.append(dict(off=file_off, va=va, text=cur.decode("ascii")))
        return strings

    def find_string_xrefs(self, str_va, search_section=".text"):
        """Find all locations in code that reference this string's VA."""
        target = struct.pack("<I", str_va)
        refs = []
        for sec in self.sections:
            if sec["name"] != search_section:
                continue
            start = sec["raw_off"]
            end = start + sec["raw_size"]
            off = start
            while True:
                off = self.data.find(target, off, end)
                if off < 0:
                    break
                refs.append(dict(file_off=off, va=self.offset_to_va(off)))
                off += 4
        return refs


def print_banner(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def section_flags_str(chars):
    flags = []
    if chars & 0x20:    flags.append("CODE")
    if chars & 0x40:    flags.append("IDATA")
    if chars & 0x80:    flags.append("UDATA")
    if chars & 0x20000000: flags.append("EXEC")
    if chars & 0x40000000: flags.append("READ")
    if chars & 0x80000000: flags.append("WRITE")
    return "|".join(flags) if flags else f"0x{chars:08X}"


def main():
    parser = argparse.ArgumentParser(description="Monkey2.exe PE Analysis")
    parser.add_argument("--strings", action="store_true", help="Dump all strings")
    parser.add_argument("--filter", default="", help="Comma-separated keywords to filter strings")
    parser.add_argument("--xrefs", metavar="KEYWORD", help="Find xrefs for strings matching keyword")
    parser.add_argument("--exe", default=EXE_PATH, help="Path to exe")
    args = parser.parse_args()

    data = load_exe(args.exe)
    pe = PEParser(data)

    print_banner("PE HEADER SUMMARY")
    arch = "x86" if pe.machine == 0x14C else "x64" if pe.machine == 0x8664 else f"0x{pe.machine:04X}"
    print(f"  Architecture : {arch}")
    print(f"  Image base   : 0x{pe.image_base:08X}")
    print(f"  Entry point  : 0x{pe.image_base + pe.entry_point_rva:08X}  (RVA 0x{pe.entry_point_rva:08X})")
    print(f"  Image size   : {pe.image_size // 1024} KB")
    print(f"  File size    : {len(data) // 1024} KB")

    print_banner("SECTIONS")
    print(f"  {'Name':<12} {'VirtAddr':>12} {'VirtSize':>10} {'RawOff':>10} {'RawSize':>10}  Flags")
    for sec in pe.sections:
        print(
            f"  {sec['name']:<12} 0x{pe.image_base+sec['vaddr']:08X}  "
            f"{sec['vsize']:>10}  0x{sec['raw_off']:08X}  {sec['raw_size']:>10}  "
            f"{section_flags_str(sec['chars'])}"
        )

    print_banner("IMPORTS")
    imports = pe.parse_imports()
    for dll, funcs in imports.items():
        print(f"\n  {dll}")
        for fn in funcs:
            print(f"    IAT=0x{fn['iat_va']:08X}  {fn['name']}")

    if args.strings or args.xrefs:
        filters = [f.lower().strip() for f in args.filter.split(",") if f.strip()]
        all_strings = pe.extract_strings(min_len=5)

        if args.xrefs:
            kw = args.xrefs.lower()
            print_banner(f"XREFS FOR STRINGS MATCHING '{kw}'")
            for s in all_strings:
                if kw in s["text"].lower() and s["va"]:
                    refs = pe.find_string_xrefs(s["va"])
                    if refs:
                        print(f"\n  String '{s['text'][:60]}' @ VA=0x{s['va']:08X}")
                        for r in refs:
                            # Show surrounding bytes for context
                            ctx_off = r["file_off"] - 4
                            ctx = bytes(data[max(0, ctx_off) : r["file_off"] + 8])
                            print(f"    Referenced @ VA=0x{r['va']:08X}  bytes: {ctx.hex()}")
        else:
            print_banner("STRINGS")
            for s in all_strings:
                if not filters or any(f in s["text"].lower() for f in filters):
                    va_str = f"VA=0x{s['va']:08X}" if s["va"] else "VA=?       "
                    print(f"  off=0x{s['off']:08X}  {va_str}  {s['text'][:100]}")


if __name__ == "__main__":
    main()

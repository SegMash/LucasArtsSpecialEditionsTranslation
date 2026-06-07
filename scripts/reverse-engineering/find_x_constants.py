"""
find_x_constants.py — Hunt for X-coordinate constants in Monkey2.exe.

Use case
--------
The conversation-choice menu in MI2 SE draws bullet markers at a fixed X
position near the left edge of the screen.  We suspect that X is stored
somewhere in the EXE as a hard-coded constant (float in .rdata or
immediate operand in .text).  This script enumerates plausible
candidates so we can narrow the search before opening Ghidra.

What it does
------------
1.  Scans `.rdata` for IEEE-754 floats whose value lies in a configurable
    "plausible UI-margin" range (default 4.0–200.0).  These are the most
    likely homes for bullet-X.
2.  For every hit it counts xrefs from `.text` that load the constant by
    address (`movss xmm,[mem]`, `fld [mem]`, plain pointer push) so we
    can immediately rank candidates by how many call-sites reference
    them.  A constant that is referenced from only ~1–4 places near the
    text-rendering region is a much better suspect than one referenced
    100 times.
3.  Optionally also scans `.text` for `push imm32` integer immediates in
    the same range (in case bullet-X is stored as an int rather than a
    float — common for older 2-D engines).
4.  Prints, for every interesting candidate, a short hex-dump around the
    xref so you can eyeball the instruction context.

Usage
-----
    python scripts/reverse-engineering/find_x_constants.py
    python scripts/reverse-engineering/find_x_constants.py --min 10 --max 100
    python scripts/reverse-engineering/find_x_constants.py --include-ints
    python scripts/reverse-engineering/find_x_constants.py --top 20

Notes
-----
This is a *static* scan only — it does NOT confirm that a candidate is
actually used by the choice-menu code.  Treat the output as a shortlist
to verify in Ghidra (right-click constant -> "References" -> see which
function loads it; check whether that function also touches the choice/
dialog text path).
"""

from __future__ import annotations

import argparse
import os
import struct
from collections import defaultdict


EXE_PATH   = os.path.join(os.path.dirname(__file__), "..", "..", "Monkey2.exe")
IMAGE_BASE = 0x00400000


# ---------------------------------------------------------------------------
# PE parsing — minimal, mirrored from find_render_routines.py
# ---------------------------------------------------------------------------

def load_and_map(path: str):
    with open(path, "rb") as f:
        data = bytearray(f.read())

    pe_off  = struct.unpack_from("<I", data, 0x3C)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz  = struct.unpack_from("<H", data, pe_off + 20)[0]
    sec_base = pe_off + 24 + opt_sz

    sections = []
    for i in range(num_sec):
        s     = sec_base + i * 40
        name  = data[s:s+8].rstrip(b"\x00").decode("ascii", errors="replace")
        vaddr = struct.unpack_from("<I", data, s + 12)[0]
        rsz   = struct.unpack_from("<I", data, s + 16)[0]
        roff  = struct.unpack_from("<I", data, s + 20)[0]
        sections.append((name, vaddr, roff, rsz))

    def va_to_off(va: int) -> int | None:
        rva = va - IMAGE_BASE
        for _, va_, ro, rs in sections:
            if va_ <= rva < va_ + rs:
                return ro + (rva - va_)
        return None

    def off_to_va(off: int) -> int | None:
        for _, va_, ro, rs in sections:
            if ro <= off < ro + rs:
                return IMAGE_BASE + va_ + (off - ro)
        return None

    def section_range(name: str) -> tuple[int, int] | None:
        for n, va_, ro, rs in sections:
            if n == name:
                return ro, ro + rs
        return None

    return data, va_to_off, off_to_va, section_range, sections


# ---------------------------------------------------------------------------
# Float candidate enumeration in .rdata
# ---------------------------------------------------------------------------

def enumerate_float_constants(
    data: bytearray,
    rdata_range: tuple[int, int],
    off_to_va,
    lo: float,
    hi: float,
):
    """Yield (va, file_off, value) for every 4-byte float in .rdata whose
    value lies in [lo, hi] AND which is 4-byte aligned (typical for the
    MSVC compiler's float-constant pool).
    """
    ro, re = rdata_range
    # 4-byte stride is the conventional alignment for float literals.
    for off in range(ro, re - 4, 4):
        b = bytes(data[off:off+4])
        # Reject pointers / RVAs that look like addresses by exclusion:
        # values >= 1e6 are almost certainly not UI margins.
        try:
            v = struct.unpack("<f", b)[0]
        except Exception:
            continue
        if v != v:  # NaN
            continue
        if not (lo <= v <= hi):
            continue
        yield off_to_va(off), off, v


# ---------------------------------------------------------------------------
# X-ref counting — how many places in .text take the address of a constant
# ---------------------------------------------------------------------------

def count_xrefs_to_constants(
    data: bytearray,
    text_range: tuple[int, int],
    constants: list[tuple[int, int, float]],
    off_to_va,
):
    """For each (va, file_off, value) in `constants`, count how many places
    inside the .text range hold the literal little-endian VA bytes — i.e.
    instructions like  movss xmm,[mem]  or  push imm32.  Returns a dict
    keyed by VA whose value is a list of xref file offsets.
    """
    ts, te = text_range
    text_blob = bytes(data[ts:te])

    refs: dict[int, list[int]] = defaultdict(list)
    for va, _const_off, _val in constants:
        needle = struct.pack("<I", va)
        start = 0
        while True:
            idx = text_blob.find(needle, start)
            if idx < 0:
                break
            refs[va].append(ts + idx)
            start = idx + 1   # allow overlapping (very rare for 4-byte VAs)
    return refs


# ---------------------------------------------------------------------------
# push imm32 scan in .text  (covers integer-typed coordinates)
# ---------------------------------------------------------------------------

def scan_push_imm32(
    data: bytearray,
    text_range: tuple[int, int],
    off_to_va,
    lo: int,
    hi: int,
) -> dict[int, list[int]]:
    """Find every  push imm32  (0x68 imm32) in .text whose immediate falls
    inside [lo, hi].  Returns {value: [list of file offsets]}.
    """
    ts, te = text_range
    hits: dict[int, list[int]] = defaultdict(list)
    for off in range(ts, te - 5):
        if data[off] != 0x68:
            continue
        v = struct.unpack_from("<I", data, off + 1)[0]
        if lo <= v <= hi:
            hits[v].append(off)
    return hits


# ---------------------------------------------------------------------------
# Pretty hex dump around a single xref
# ---------------------------------------------------------------------------

def context_dump(data: bytearray, off: int, off_to_va, before: int = 6, after: int = 10) -> str:
    start = max(0, off - before)
    end   = min(len(data), off + after)
    row   = bytes(data[start:end])
    va    = off_to_va(off)
    return f"VA 0x{va:08X}  ctx [{row.hex()}]"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exe", default=EXE_PATH, help="Path to the EXE.")
    p.add_argument("--min", type=float, default=4.0,
                   help="Minimum candidate value (default 4.0).")
    p.add_argument("--max", type=float, default=200.0,
                   help="Maximum candidate value (default 200.0 — left half "
                        "of a 1280-wide screen, with a generous safety margin).")
    p.add_argument("--top", type=int, default=30,
                   help="Show the N candidates with the FEWEST xrefs first "
                        "(narrowest = most-suspect first).  Default 30.")
    p.add_argument("--max-refs", type=int, default=5,
                   help="Skip candidates that are referenced more often than "
                        "this — they are unlikely to be a single UI margin "
                        "constant.  Default 5.")
    p.add_argument("--include-ints", action="store_true",
                   help="Also scan .text for `push imm32` integer immediates "
                        "in the same numeric range.")
    args = p.parse_args()

    print(f"Loading {args.exe} ...")
    data, va_to_off, off_to_va, sec_range, sections = load_and_map(args.exe)

    print("Sections:")
    for name, va, ro, rs in sections:
        print(f"  {name:<10}  VA 0x{IMAGE_BASE + va:08X}  raw 0x{ro:08X}  size {rs}")

    text_range  = sec_range(".text")
    rdata_range = sec_range(".rdata")
    if text_range is None or rdata_range is None:
        raise SystemExit("ERROR: .text or .rdata section not found in PE.")

    # ---------- 1. enumerate float candidates --------------------------------
    print(f"\nScanning .rdata for floats in [{args.min}, {args.max}] ...")
    candidates = list(
        enumerate_float_constants(data, rdata_range, off_to_va, args.min, args.max)
    )
    print(f"  Found {len(candidates)} float candidates.")

    # ---------- 2. count xrefs from .text ------------------------------------
    print("Counting xrefs from .text ...")
    refs_map = count_xrefs_to_constants(data, text_range, candidates, off_to_va)

    # Build (va, value, xref_count) sorted by xref_count ASC, then value ASC.
    enriched = []
    for va, _off, val in candidates:
        n = len(refs_map.get(va, []))
        if n == 0 or n > args.max_refs:
            continue   # zero refs = unused literal; >max-refs = too generic
        enriched.append((va, val, n))

    enriched.sort(key=lambda x: (x[2], x[1]))

    # ---------- 3. report ----------------------------------------------------
    print("\n" + "=" * 78)
    print(f"  TOP {min(args.top, len(enriched))} float candidates  "
          f"(low xref count = high suspicion of being a single UI constant)")
    print("=" * 78)

    if not enriched:
        print(
            f"\n  No float in .rdata fell into [{args.min}, {args.max}] with "
            f"1..{args.max_refs} xrefs.  Try widening the range with --min/--max."
        )
    else:
        for va, val, n in enriched[: args.top]:
            print(f"\n  RDATA VA 0x{va:08X}  =  {val:.4f}  ({n} xref{'s' if n != 1 else ''} from .text)")
            for ref_off in refs_map[va][:6]:
                print(f"      {context_dump(data, ref_off, off_to_va)}")

    # ---------- 4. integer immediates ----------------------------------------
    if args.include_ints:
        print("\n" + "=" * 78)
        print(f"  PUSH imm32 INTEGERS in .text within [{int(args.min)}, "
              f"{int(args.max)}]  (potential int-typed bullet X)")
        print("=" * 78)
        int_hits = scan_push_imm32(
            data, text_range, off_to_va, int(args.min), int(args.max)
        )
        # Sort by frequency ascending — rare values are more interesting.
        for v in sorted(int_hits, key=lambda k: (len(int_hits[k]), k)):
            n = len(int_hits[v])
            if n > args.max_refs:
                continue
            print(f"\n  push 0x{v:X}  (= {v})   {n} site{'s' if n != 1 else ''}")
            for off in int_hits[v][:6]:
                print(f"      {context_dump(data, off, off_to_va)}")

    print("\nDone.")
    print(
        "\nNext step: open Ghidra, navigate to one of the printed RDATA VAs,"
        "\nright-click -> References -> see which function reads it.  If the"
        "\ncalling function is also called near the conversation/choice menu"
        "\ncode (e.g. through DrawString call-sites), you have your bullet X."
    )


if __name__ == "__main__":
    main()

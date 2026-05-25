"""Byte-compare two .font files and report all differences with context."""
import sys
import os

def compare(path_a, path_b):
    a = open(path_a, "rb").read()
    b = open(path_b, "rb").read()

    print(f"File A: {path_a}  ({len(a)} bytes)")
    print(f"File B: {path_b}  ({len(b)} bytes)")

    GLYPH_REC = 16992
    sections = [
        (0,     90,       "Header"),
        (90,    400,      "Primary char table (cp31..185)"),
        (400,   GLYPH_REC,"Extended char table (cp186+)"),
        (GLYPH_REC, len(a), "Glyph metric records"),
    ]

    total_diffs = 0
    for sec_start, sec_end, sec_name in sections:
        diffs = []
        for i in range(sec_start, min(sec_end, len(a), len(b))):
            if a[i] != b[i]:
                diffs.append(i)
        if diffs:
            print(f"\n  Section: {sec_name}  ({sec_start:#x}..{sec_end:#x})")
            # Group contiguous runs
            runs, run = [], [diffs[0]]
            for d in diffs[1:]:
                if d == run[-1] + 1:
                    run.append(d)
                else:
                    runs.append(run); run = [d]
            runs.append(run)
            for r in runs[:20]:  # show first 20 runs
                start, end = r[0], r[-1]
                ctx_a = " ".join(f"{a[j]:02x}" for j in range(start, end+1))
                ctx_b = " ".join(f"{b[j]:02x}" for j in range(start, end+1))
                print(f"    offset {start:#07x} .. {end:#07x}  ({len(r)} bytes)")
                print(f"      A: {ctx_a}")
                print(f"      B: {ctx_b}")
            if len(runs) > 20:
                print(f"    ... and {len(runs)-20} more runs")
        else:
            print(f"  Section: {sec_name}  -- IDENTICAL")
        total_diffs += len(diffs)

    print(f"\nTotal differing bytes: {total_diffs}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: compare_fonts.py <file_a> <file_b>")
        sys.exit(1)
    compare(sys.argv[1], sys.argv[2])

"""
Find lines in a UTF-8 text file longer than a given character count.

Usage:
    python scripts/text/find_long_lines.py translations/m1/heb/he.speech.txt 78
    python scripts/text/find_long_lines.py input.txt --max 78
    python scripts/text/find_long_lines.py input.txt 78 --out long_lines.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def find_long_lines(path: Path, max_len: int) -> list[tuple[int, int, str]]:
    """Return (1-based line number, length, text) for lines longer than max_len."""
    results: list[tuple[int, int, str]] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            text = line.rstrip("\r\n")
            length = len(text)
            if length > max_len:
                #If line contains \n ignore it
                if '\\n' in text:
                    continue
                results.append((i, length, text))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List lines longer than X characters in a text file."
    )
    parser.add_argument("txt", help="Path to the UTF-8 text file")
    parser.add_argument(
        "max_len",
        nargs="?",
        type=int,
        default=None,
        help="Maximum allowed line length (lines longer than this are reported)",
    )
    parser.add_argument(
        "--max",
        dest="max_opt",
        type=int,
        default=None,
        help="Same as positional max_len (alternative flag form)",
    )
    parser.add_argument(
        "--out",
        help="Optional path to write the report (UTF-8)",
    )
    args = parser.parse_args()

    max_len = args.max_opt if args.max_opt is not None else args.max_len
    if max_len is None:
        parser.error("Provide max length as positional arg or --max")
    if max_len < 0:
        parser.error("max length must be >= 0")

    path = Path(args.txt)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    hits = find_long_lines(path, max_len)
    lines_out = [
        f"Line {num} ({length} chars): {text}" for num, length, text in hits
    ]
    summary = f"{path}: {len(hits)} line(s) longer than {max_len}"

    report = "\n".join(lines_out)
    if report:
        print(report)
    print(summary)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(
            (report + ("\n" if report else "") + summary + "\n"),
            encoding="utf-8",
        )
        print(f"Wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

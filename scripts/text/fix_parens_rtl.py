"""
Fix parentheses for RTL display: (word) -> )word(

The game engine / RTL path shows (foo) as )foo(, so pre-flip the
parens in the source text so they display correctly.

Matches parentheses wrapping a non-empty run of non-paren characters
(letters, digits, punctuation like "...", spaces, etc.).

Protected segments matching {...} or `...` are left unchanged.

Usage:
    python scripts/text/fix_parens_rtl.py translations/m1/heb/he.speech.txt --dry-run
    python scripts/text/fix_parens_rtl.py translations/m1/heb/he.speech.txt
    python scripts/text/fix_parens_rtl.py input.txt --out output.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TOKEN_RE = re.compile(r"(\{[^}]+\}|`[^`]*`)")
# ( ... ) where inner has no parentheses
_PAREN_RE = re.compile(r"\(([^()]+)\)")


def fix_parens_in_text(text: str) -> tuple[str, int]:
    """Return (new_text, number_of_paren_groups_fixed)."""
    count = 0
    parts: list[str] = []

    for seg in _TOKEN_RE.split(text):
        if _TOKEN_RE.fullmatch(seg):
            parts.append(seg)
            continue

        def _flip(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"){m.group(1)}("

        parts.append(_PAREN_RE.sub(_flip, seg))

    return "".join(parts), count


def process_file(path: Path, out_path: Path | None, dry_run: bool) -> int:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    changed_lines = 0
    total_fixes = 0
    new_lines: list[str] = []

    for line in lines:
        if line.endswith("\r\n"):
            body, eol = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, eol = line[:-1], "\n"
        elif line.endswith("\r"):
            body, eol = line[:-1], "\r"
        else:
            body, eol = line, ""

        new_body, n = fix_parens_in_text(body)
        total_fixes += n
        if new_body != body:
            changed_lines += 1
        new_lines.append(new_body + eol)

    new_text = "".join(new_lines)
    print(f"{path}: fixed {total_fixes} paren group(s) on {changed_lines} line(s)")

    if dry_run:
        return 0

    dest = out_path or path
    dest.write_text(new_text, encoding="utf-8", newline="")
    print(f"Wrote {dest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite (word) to )word( for RTL parenthesis display."
    )
    parser.add_argument("txt", help="Path to the UTF-8 text file")
    parser.add_argument(
        "--out",
        help="Write result to this path (default: overwrite input)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing",
    )
    args = parser.parse_args()

    path = Path(args.txt)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else None
    return process_file(path, out_path, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

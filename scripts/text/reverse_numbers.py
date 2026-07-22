"""
Reverse digit sequences in a UTF-8 text file.

Used for RTL / inject-time reversal: if the display path reverses the whole
string, numbers like 100 become 001 unless their digits are pre-reversed here.

Only contiguous digit runs (0-9) are reversed. Protected segments matching
{...} or `...` are left unchanged.

Usage:
    python scripts/text/reverse_numbers.py translations/m1/heb/he.speech.txt
    python scripts/text/reverse_numbers.py input.txt --out output.txt
    python scripts/text/reverse_numbers.py input.txt --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TOKEN_RE = re.compile(r'(\{[^}]+\}|`[^`]*`)')
_DIGITS_RE = re.compile(r'\d+')


def reverse_numbers_in_text(text: str) -> tuple[str, int]:
    """Return (new_text, number_of_digit_runs_reversed)."""
    count = 0
    parts: list[str] = []

    for seg in _TOKEN_RE.split(text):
        if _TOKEN_RE.fullmatch(seg):
            parts.append(seg)
            continue

        def _rev(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return m.group(0)[::-1]

        parts.append(_DIGITS_RE.sub(_rev, seg))

    return ''.join(parts), count


def process_file(path: Path, out_path: Path | None, dry_run: bool) -> int:
    original = path.read_text(encoding='utf-8')
    # Preserve final newline style: process line-by-line so line endings stay.
    lines = original.splitlines(keepends=True)
    changed_lines = 0
    total_runs = 0
    new_lines: list[str] = []

    for line in lines:
        # Keep EOL separate so we only transform content.
        if line.endswith('\r\n'):
            body, eol = line[:-2], '\r\n'
        elif line.endswith('\n'):
            body, eol = line[:-1], '\n'
        elif line.endswith('\r'):
            body, eol = line[:-1], '\r'
        else:
            body, eol = line, ''

        new_body, n = reverse_numbers_in_text(body)
        total_runs += n
        if new_body != body:
            changed_lines += 1
        new_lines.append(new_body + eol)

    new_text = ''.join(new_lines)
    print(f"{path}: reversed {total_runs} number(s) on {changed_lines} line(s)")

    if dry_run:
        return 0

    dest = out_path or path
    dest.write_text(new_text, encoding='utf-8', newline='')
    print(f"Wrote {dest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Reverse digit sequences in a text file (for RTL number display).'
    )
    parser.add_argument('txt', help='Path to the UTF-8 text file')
    parser.add_argument(
        '--out',
        help='Write result to this path (default: overwrite input)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report changes without writing',
    )
    args = parser.parse_args()

    path = Path(args.txt)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else None
    return process_file(path, out_path, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())

"""
create_mapping.py

Reads samples/english.txt (UTF-8) and samples/hebrew.txt (Windows-1255)
line-by-line (they are in sync), strips special noise characters from both,
and writes a mapping file:

    <english line> === <hebrew line>

Noise removed from every line (both files):
  - Literal escape sequences of the form \\xHH  (backslash + x + 2 hex digits)
  - The @ character

Output is written as Windows-1255 so Hebrew text is preserved correctly.

Usage:
    python scripts/text/create_mapping.py
        [--english  samples/english.txt]
        [--hebrew   samples/hebrew.txt]
        [--output   samples/mapping.txt]
"""

import re
import argparse
from pathlib import Path

# Matches the literal 4-character sequence  \xHH  (e.g. \xFF, \x0A, \x2D)
_NOISE_RE = re.compile(r'\\x[0-9A-Fa-f]{2}')


def clean(line: str) -> str:
    """Remove noise characters and return a stripped line."""
    line = _NOISE_RE.sub('', line)
    line = line.replace('@', '')
    line = line.replace('^', '...')
    return line.strip()


def build_mapping(english_path: Path, hebrew_path: Path, output_path: Path) -> None:
    with (
        english_path.open(encoding='utf-8', errors='replace') as en_f,
        hebrew_path.open(encoding='windows-1255', errors='replace') as he_f,
        output_path.open('w', encoding='windows-1255', errors='replace') as out_f,
    ):
        for lineno, (en_line, he_line) in enumerate(zip(en_f, he_f), start=1):
            en_clean = clean(en_line)
            he_clean = clean(he_line)
            if en_clean and he_clean:
                out_f.write(f'{en_clean} === {he_clean}\n')

    print(f'Mapping written to {output_path}')


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    parser = argparse.ArgumentParser(description='Create English-Hebrew mapping file.')
    parser.add_argument('--english',  default=str(repo_root / 'samples' / 'english.txt'))
    parser.add_argument('--hebrew',   default=str(repo_root / 'samples' / 'hebrew.txt'))
    parser.add_argument('--output',   default=str(repo_root / 'samples' / 'mapping.txt'))
    parser.add_argument('--override', action='store_true',
                        help='Overwrite the output file if it already exists')
    args = parser.parse_args()

    english_path = Path(args.english)
    hebrew_path  = Path(args.hebrew)
    output_path  = Path(args.output)

    if not english_path.exists():
        raise FileNotFoundError(f'English file not found: {english_path}')
    if not hebrew_path.exists():
        raise FileNotFoundError(f'Hebrew file not found: {hebrew_path}')

    if output_path.exists() and not args.override:
        raise FileExistsError(
            f'Output file already exists: {output_path}\n'
            'Use --override to overwrite it.'
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_mapping(english_path, hebrew_path, output_path)


if __name__ == '__main__':
    main()

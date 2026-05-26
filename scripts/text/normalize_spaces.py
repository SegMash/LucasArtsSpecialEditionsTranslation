"""
Finds lines in the mapping file where the English side contains two or more
consecutive spaces, then appends a normalized copy of each such entry (with all
runs of multiple spaces collapsed to a single space) to the end of the mapping
file so the injection script can match both the padded and the normalized form.

Entries whose normalized English key already exists in the mapping are skipped
to avoid duplicates.

Usage:
    python scripts/text/normalize_spaces.py [mapping_file]

Arguments:
    mapping_file  Path to the mapping file (Windows-1255 encoded).
                  Default: translations/mapping.txt
"""

import re
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
_MULTI_SPACE = re.compile(r' {2,}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Append space-normalized duplicates of multi-space English entries.'
    )
    parser.add_argument(
        'mapping_file',
        nargs='?',
        default=str(BASE_DIR / 'translations' / 'mapping.txt'),
        help='Path to the mapping file (default: translations/mapping.txt)',
    )
    args = parser.parse_args()

    mapping_path = Path(args.mapping_file)
    if not mapping_path.exists():
        raise FileNotFoundError(f'Mapping file not found: {mapping_path}')

    # Load all existing English keys so we can detect duplicates
    existing_keys: set[str] = set()
    entries: list[tuple[str, str]] = []  # (en, he) for multi-space lines

    with mapping_path.open(encoding='windows-1255', errors='replace') as f:
        for line in f:
            line = line.rstrip('\r\n')
            if ' === ' not in line:
                continue
            en, _, he = line.partition(' === ')
            existing_keys.add(en)
            if '  ' in en:
                entries.append((en, he.strip()))

    # Build new entries: normalized English that isn't already in the mapping
    new_entries: list[tuple[str, str]] = []
    for en, he in entries:
        normalized_en = _MULTI_SPACE.sub(' ', en).strip()
        normalized_he = _MULTI_SPACE.sub(' ', he).strip()
        if normalized_en not in existing_keys and normalized_en:
            new_entries.append((normalized_en, normalized_he))
            existing_keys.add(normalized_en)  # prevent duplicates within this run

    if not new_entries:
        print('Nothing to add — all normalized forms already exist in the mapping.')
        return

    with mapping_path.open('a', encoding='windows-1255', errors='replace') as f:
        for en, he in new_entries:
            f.write(f'{en} === {he}\n')  # en/he are already normalized

    print(f'Appended {len(new_entries)} normalized entr{"y" if len(new_entries) == 1 else "ies"}.')
    for en, he in new_entries:
        print(f'  {en!r}  =>  {he!r}')


if __name__ == '__main__':
    main()

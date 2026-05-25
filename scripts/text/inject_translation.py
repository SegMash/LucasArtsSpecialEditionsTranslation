"""
STEP 4 of the translation pipeline.

Reads a mapping file (English === Hebrew, Windows-1255 encoded) and rebuilds
{TARGET_LANG}.speech.info and {TARGET_LANG}.uitext.info with Hebrew text
replacing the language text.

For each triplet [SOUND_ID, EN_TEXT, LANG_TEXT]:
  - Looks up EN_TEXT in the translation table -> HE_TEXT
  - If no Hebrew available, falls back to EN_TEXT
  - Rebuilds the data section with HE_TEXT in place of LANG_TEXT
  - Recalculates ALL pointer fields (F5, F6, F7) using cumulative delta shifting

Set TARGET_LANG = "en" to patch the English files (original English voices).
Set TARGET_LANG = "de" to patch the German files (German voices).

Pointer update rule (RECORD_SIZE=32, 8 x uint32 per record):
  F6[i] = F5[i] + (SOUND_ID_len[i] - 3)
  F7[i] = F6[i] + (EN_TEXT_len[i] - 3)
  F5[i+1] = F5[i] + triplet_total_bytes[i] - 32
  F5[0] is an anchor that stays constant.

Hebrew encoding:
  The game does not use UTF-8 for Hebrew.  Each Hebrew letter is stored as a
  custom single byte code defined in scripts/fonts/hebrew_mapping.py.
  ASCII characters (space, punctuation, numbers, English letters) are stored
  as their standard ASCII byte values unchanged.

Output files:
  <loc_dir>/{TARGET_LANG}.speech.info   (overwritten)
  <loc_dir>/{TARGET_LANG}.uitext.info   (overwritten)
  (originals are backed up as .bak before writing)

Usage:
    python inject_translation.py <loc_dir> <mapping_file> [--dry-run]

Arguments:
    loc_dir       Path to the directory containing the .info files
                  (e.g. quickbms/output/localization)
    mapping_file  Path to the mapping file with lines:
                  <english text> === <hebrew text>
                  encoded as Windows-1255  (e.g. translations/mapping.txt)
"""

import struct
import os
import sys
import io
import argparse
import re
import shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fix_argv() -> None:
    """Work around the PowerShell trailing-backslash quoting bug.

    When a quoted path ends with a backslash (e.g. "C:\\path\\dir\\") PowerShell
    treats the \\" as an escaped quote, merging the rest of the command line into
    that one argument.  We detect the embedded " and re-split the argument.
    """
    fixed = [sys.argv[0]]
    for arg in sys.argv[1:]:
        if '"' in arg:
            before, _, after = arg.partition('"')
            fixed.append(before.rstrip("\\"))
            fixed.extend(after.strip().split())
        else:
            fixed.append(arg)
    sys.argv[:] = fixed


_fix_argv()

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Inject Hebrew translations into .speech.info / .uitext.info files."
)
_parser.add_argument(
    "loc_dir",
    nargs="?",
    default=os.path.join(BASE_DIR, "quickbms", "output", "localization"),
    help="Directory containing the TARGET_LANG .info files "
         "(default: quickbms/output/localization)",
)
_parser.add_argument(
    "mapping_file",
    nargs="?",
    default=os.path.join(BASE_DIR, "translations", "mapping.txt"),
    help="Path to the mapping file, Windows-1255, lines: english === hebrew "
         "(default: translations/mapping.txt)",
)
_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Parse and report without writing any files",
)
_args = _parser.parse_args()

LOC_DIR = _args.loc_dir.rstrip("\\/")
DRY_RUN = _args.dry_run

# Language whose localization files we are replacing with Hebrew.
TARGET_LANG = "en"

# Load hebrew_mapping by absolute file path
import importlib.util as _il
_mapping_path = os.path.join(BASE_DIR, "scripts", "fonts", "hebrew_mapping.py")
_spec = _il.spec_from_file_location("hebrew_mapping", _mapping_path)
_mod = _il.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
HEBREW_TO_CODE: dict[str, int] = _mod.HEBREW_TO_CODE

RECORD_SIZE = 32

# Hebrew text stored in logical order; the engine patch reverses each line.
REVERSE_FOR_LTR = False

# Regex that matches "protected" segments which must NOT be reversed:
_TOKEN_RE = re.compile(r'(\{[^}]+\}|`[^`]*`)')

_encode_warnings: list[str] = []


def encode_he_text(text: str) -> bytes:
    """Encode a Hebrew/mixed string to the game's custom single-byte encoding."""
    segments = _TOKEN_RE.split(text)

    if REVERSE_FOR_LTR:
        segments = segments[::-1]

    result = bytearray()
    for seg in segments:
        if _TOKEN_RE.fullmatch(seg):
            for ch in seg:
                code = ord(ch)
                if 0x20 <= code <= 0x7E:
                    result.append(code)
        else:
            chars = list(seg)
            if REVERSE_FOR_LTR:
                chars = chars[::-1]
            for ch in chars:
                if ch in HEBREW_TO_CODE:
                    result.append(HEBREW_TO_CODE[ch])
                elif 0x20 <= ord(ch) <= 0x7E:
                    result.append(ord(ch))
                elif ord(ch) == 0x0A:
                    result.append(0x0A)
                elif ord(ch) != 0:
                    _encode_warnings.append(ch)

    return bytes(result)


def reverse_ascii_bytes(raw: bytes) -> bytes:
    """Reverse raw ASCII bytes while keeping {token} segments intact."""
    text = raw.decode("ascii", errors="replace")
    segments = _TOKEN_RE.split(text)
    segments = segments[::-1]
    result = bytearray()
    for seg in segments:
        if _TOKEN_RE.fullmatch(seg):
            result.extend(seg.encode("ascii", errors="replace"))
        else:
            result.extend(seg[::-1].encode("ascii", errors="replace"))
    return bytes(result)


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def scan_strings(data: bytes, start: int) -> list[tuple[int, str, bytes]]:
    """Returns list of (offset, text, raw_bytes) for each null-terminated string."""
    results, pos, n = [], start, len(data)
    while pos < n:
        end = pos
        while end < n and data[end] != 0:
            end += 1
        raw = data[pos:end]
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", "replace")
        results.append((pos, text, raw))
        pos = end + 1
    return results


def parse_triplets(strings, rc: int):
    """Parse strings into triplets [SOUND_ID, EN_TEXT, LANG_TEXT].
    Uses strict stride-3 for the first rc*3 strings (matching the record count)."""
    triplets = []
    count = min(rc * 3, len(strings))
    for i in range(0, count - 2, 3):
        o0, s0, r0 = strings[i]
        o1, s1, r1 = strings[i + 1]
        o2, s2, r2 = strings[i + 2]
        triplets.append((o0, s0, r0, o1, s1, r1, o2, s2, r2))
    return triplets




# ── Rebuild speech.info ───────────────────────────────────────────────────────

def rebuild_speech(table: dict[str, str]) -> tuple[int, int, int]:
    """Rebuild speech.info with variable-length Hebrew text.

    RECORD_SIZE = 32 bytes (8 x uint32).
    Verified pointer formulas (100% across all 8725 EN records):
      F6[i] = F5[i] + (SOUND_ID_len[i] - 3)
      F7[i] = F6[i] + (EN_TEXT_len[i] - 3)
      F5[i+1] = F5[i] + triplet_total_bytes[i] - 32

    F7 encodes EN_TEXT length (unchanged), so only F5 needs recalculation
    when LANG_TEXT length changes. F6 and F7 follow from new F5.
    """
    path = os.path.join(LOC_DIR, f"{TARGET_LANG}.speech.info")
    with open(path, "rb") as f:
        data = f.read()

    rc = u32(data, 0)
    data_start = 4 + rc * RECORD_SIZE
    index_bytes = bytearray(data[4:data_start])

    strings = scan_strings(data, data_start)
    triplets = parse_triplets(strings, rc)

    print(f"{TARGET_LANG}.speech.info: {rc} records, {len(triplets)} triplets")

    # Phase 1: Rebuild data section, collect per-triplet metadata
    new_data = bytearray()
    translated = fallback = empty_count = 0

    # Per triplet: (snd_len, en_len, new_triplet_total_bytes)
    triplet_meta: list[tuple[int, int, int]] = []

    for o0, s0, r0, o1, s1, r1, o2, s2, r2 in triplets:
        sound_bytes = r0 + b"\x00"
        en_bytes = r1 + b"\x00"

        if s1 in table and table[s1]:
            encoded = encode_he_text(table[s1])
            translated += 1
        elif s1:
            encoded = reverse_ascii_bytes(r1) if not REVERSE_FOR_LTR else r1
            fallback += 1
        else:
            encoded = b""
            empty_count += 1

        he_bytes = encoded + b"\x00"

        new_data += sound_bytes + en_bytes + he_bytes
        triplet_meta.append((len(r0), len(r1), len(sound_bytes) + len(en_bytes) + len(he_bytes)))

    # Trailing data after last triplet (extra strings beyond rc*3)
    if triplets:
        last_trip_end = triplets[-1][6] + len(triplets[-1][8]) + 1
        if last_trip_end < len(data):
            new_data += data[last_trip_end:]

    print(f"  Translated: {translated}, English fallback: {fallback}, Empty: {empty_count}")

    # Phase 2: Recompute F5, F6, F7 using the recurrence relation
    # F5[0] stays the same (anchor). Each subsequent F5 is derived from the previous.
    orig_f5_0 = struct.unpack_from("<I", index_bytes, 5 * 4)[0]
    current_f5 = orig_f5_0
    pointer_updates = 0

    for i in range(rc):
        base = i * RECORD_SIZE
        if i < len(triplet_meta):
            snd_len, en_len, new_trip_bytes = triplet_meta[i]
        else:
            break

        new_f5 = current_f5
        new_f6 = new_f5 + (snd_len - 3)
        new_f7 = new_f6 + (en_len - 3)

        old_f5 = struct.unpack_from("<I", index_bytes, base + 5 * 4)[0]
        old_f6 = struct.unpack_from("<I", index_bytes, base + 6 * 4)[0]
        old_f7 = struct.unpack_from("<I", index_bytes, base + 7 * 4)[0]

        if new_f5 != old_f5 or new_f6 != old_f6 or new_f7 != old_f7:
            struct.pack_into("<I", index_bytes, base + 5 * 4, new_f5)
            struct.pack_into("<I", index_bytes, base + 6 * 4, new_f6)
            struct.pack_into("<I", index_bytes, base + 7 * 4, new_f7)
            pointer_updates += 1

        # Recurrence: advance F5 for next record
        current_f5 = new_f5 + new_trip_bytes - RECORD_SIZE

    print(f"  Pointer records updated: {pointer_updates} / {rc}")

    # Phase 3: Assemble final file
    new_file = pack_u32(rc) + bytes(index_bytes) + bytes(new_data)
    orig_size = len(data)
    new_size = len(new_file)
    print(f"  Original size: {orig_size:,} bytes -> New size: {new_size:,} bytes  (delta: {new_size-orig_size:+,})")

    if not DRY_RUN:
        bak = path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            print(f"  Backup: {bak}")
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback, empty_count


# ── Rebuild uitext.info ───────────────────────────────────────────────────────

def rebuild_uitext(table: dict[str, str]) -> tuple[int, int]:
    """Rebuild uitext.info — kept as in-place patch (no truncation issue here)."""
    path = os.path.join(LOC_DIR, f"{TARGET_LANG}.uitext.info")
    with open(path, "rb") as f:
        data = f.read()

    rc = u32(data, 0)
    data_start = 4 + rc * RECORD_SIZE

    strings = scan_strings(data, data_start)

    # Pairs: stride-2 (EN_TEXT, LANG_TEXT)
    pairs = []
    for i in range(0, len(strings) - 1, 2):
        o_en, en, r_en = strings[i]
        o_lang, lang, r_lang = strings[i + 1]
        pairs.append((o_en, en, r_en, o_lang, lang, r_lang))

    print(f"\n{TARGET_LANG}.uitext.info: {rc} records, {len(pairs)} EN/LANG pairs")

    # In-place patch: overwrite only the LANG slot (null-pad or truncate)
    new_data = bytearray(data[data_start:])
    translated = fallback = 0

    for o_en, en, r_en, o_lang, lang, r_lang in pairs:
        old_len = len(r_lang) + 1
        section_off = o_lang - data_start

        if en in table and table[en]:
            encoded = encode_he_text(table[en])
            translated += 1
        else:
            encoded = reverse_ascii_bytes(r_en) if not REVERSE_FOR_LTR else r_en
            fallback += 1

        content_space = old_len - 1
        if len(encoded) <= content_space:
            patched = encoded + b'\x00' * (old_len - len(encoded))
        else:
            patched = encoded[:content_space] + b'\x00'

        new_data[section_off: section_off + old_len] = patched

    print(f"  Translated: {translated}, English fallback: {fallback}")

    # Index section unchanged
    index_bytes = data[4:data_start]
    new_file = pack_u32(rc) + index_bytes + bytes(new_data)
    orig_size = len(data)
    new_size = len(new_file)
    print(f"  Original size: {orig_size:,} bytes -> New size: {new_size:,} bytes  (delta: {new_size-orig_size:+,})")

    if not DRY_RUN:
        bak = path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            print(f"  Backup: {bak}")
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback


# ── Main ──────────────────────────────────────────────────────────────────────

def load_mapping(mapping_path: str) -> dict[str, str]:
    """Load a Windows-1255 mapping file (english === hebrew) into a dict."""
    table: dict[str, str] = {}
    with open(mapping_path, encoding="windows-1255", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip("\r\n")
            if " === " not in line:
                continue
            en, _, he = line.partition(" === ")
            en = en.strip()
            he = he.strip()
            if en:
                table[en] = he
    return table


def main():
    mapping_path = _args.mapping_file
    if not os.path.exists(mapping_path):
        print(f"ERROR: mapping file not found: {mapping_path}")
        return

    table = load_mapping(mapping_path)

    print(f"Translation table loaded: {len(table)} entries")
    print(f"Encoding: REVERSE_FOR_LTR={REVERSE_FOR_LTR}")
    if DRY_RUN:
        print("*** DRY RUN MODE - no files will be written ***\n")

    s_trans, s_fall, s_empty = rebuild_speech(table)
    u_trans, u_fall = rebuild_uitext(table)

    total_entries = s_trans + s_fall + s_empty
    pct = s_trans * 100 // total_entries if total_entries else 0

    print()
    print("=" * 60)
    print("INJECTION COMPLETE")
    print("=" * 60)
    print(f"  Speech: {s_trans} Hebrew, {s_fall} English fallback, {s_empty} empty")
    print(f"  UItext: {u_trans} Hebrew, {u_fall} English fallback")
    print(f"  Overall speech coverage: {pct}% Hebrew")

    if _encode_warnings:
        unique_unknown = sorted(set(_encode_warnings))
        print(f"\n  WARNING: {len(_encode_warnings)} characters skipped (no mapping).")
        print(f"  Unique unknown chars: {unique_unknown}")
        print(f"  Add them to HEBREW_TO_CODE in scripts/fonts/hebrew_mapping.py if needed.")

    if not DRY_RUN:
        print()
        print("NEXT STEP: Repack the modified files into monkey2.pak")


if __name__ == "__main__":
    main()

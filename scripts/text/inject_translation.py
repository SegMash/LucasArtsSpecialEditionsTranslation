"""
STEP 4 of the translation pipeline.

Rebuilds {TARGET_LANG}.speech.info and {TARGET_LANG}.uitext.info using the
LINE-SYNCED Hebrew text files produced by build_translation.py and (optionally)
hand-edited afterwards.

Pipeline:
    extract_text.py       .info  -> en.speech.txt / en.uitext.txt   (ordered)
    build_translation.py  en.txt -> he.speech.txt / he.uitext.txt   (auto)
    (hand-edit)           he.*.txt for context-specific translations
    inject_translation.py he.txt -> rewritten .info files            <- this script

For each record:
  - Read the Hebrew text from the matching line of he.speech.txt / he.uitext.txt
  - If the Hebrew line is identical to the English text (verbatim fallback that
    build_translation.py left in because no translation was found), encode it
    as reversed ASCII so the DrawString wrapper restores the LTR orientation.
  - Otherwise encode as Hebrew via the game's custom single-byte encoding.
  - Rebuild the binary index with recalculated pointers (no truncation, no
    padding — same logic as the previous mapping-based version).

Output files (overwritten — originals are restored from .bak before each run):
  <loc_dir>/{TARGET_LANG}.speech.info
  <loc_dir>/{TARGET_LANG}.uitext.info

Usage:
    python inject_translation.py [<loc_dir>] [--he-dir DIR]
                                 [--dry-run] [--report [FILE]]

Arguments:
    loc_dir       Directory containing the .info files
                  (default: quickbms/output/localization)
    --he-dir      Directory containing he.speech.txt / he.uitext.txt
                  (default: translations/mi2)
    --dry-run     Parse + report without writing any .info files
    --report      Write a report of records that fell back to English
                  (default path: translations/missing_translations.txt when
                   the flag is given without a value)
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
    description="Inject Hebrew translations into .speech.info / .uitext.info files "
                "using the line-synced he.*.txt sources."
)
_parser.add_argument(
    "loc_dir",
    nargs="?",
    default=os.path.join(BASE_DIR, "quickbms", "output", "localization"),
    help="Directory containing the TARGET_LANG .info files "
         "(default: quickbms/output/localization)",
)
_parser.add_argument(
    "--he-dir",
    dest="he_dir",
    default=os.path.join(BASE_DIR, "translations", "mi2"),
    help="Directory containing he.speech.txt / he.uitext.txt "
         "(default: translations/mi2)",
)
_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Parse and report without writing any files",
)
_parser.add_argument(
    "--report",
    nargs="?",
    const=os.path.join(BASE_DIR, "translations", "missing_translations.txt"),
    default=None,
    metavar="FILE",
    help="Write a report of records that fell back to English to FILE "
         "(default path: translations/missing_translations.txt)",
)
_args = _parser.parse_args()

LOC_DIR = _args.loc_dir.rstrip("\\/")
HE_DIR  = _args.he_dir.rstrip("\\/")
DRY_RUN = _args.dry_run

# Language whose localization files we are replacing with Hebrew.
TARGET_LANG = "en"

# Load hebrew_mapping by absolute file path (custom single-byte encoding table).
import importlib.util as _il
_mapping_path = os.path.join(BASE_DIR, "scripts", "fonts", "hebrew_mapping.py")
_spec = _il.spec_from_file_location("hebrew_mapping", _mapping_path)
_mod = _il.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
HEBREW_TO_CODE: dict[str, int] = _mod.HEBREW_TO_CODE

RECORD_SIZE = 32        # speech.info: 8 × uint32 per record
UITEXT_RECORD_SIZE = 8  # uitext.info: 2 × uint32 per record (KEY, DISPLAY_TEXT)

# Hebrew text stored in logical order; the engine patch reverses each line.
REVERSE_FOR_LTR = False

# Regex that matches "protected" segments which must NOT be reversed:
import re
_TOKEN_RE = re.compile(r'(\{[^}]+\}|`[^`]*`)')

_encode_warnings: list[str] = []


# ── Newline (un)escape ───────────────────────────────────────────────────────
# extract_text.py and build_translation.py keep \n / \r escaped as literal
# "\\n" / "\\r" in the .txt files so each message stays on a single line.
# At injection time we restore the real bytes before encoding.

def _unescape_newlines(text: str) -> str:
    return text.replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\r", "\r")


def _is_english_fallback(he_text: str, en_text: str) -> bool:
    """True when the Hebrew line is just a verbatim copy of the English line.

    build_translation.py writes the English text on lines it could not
    translate, so we use that as the signal to switch to the LTR-reversal
    encoding path.  Empty Hebrew is also treated as a fallback.
    """
    return (not he_text) or (he_text == en_text)


# ── Encoding ─────────────────────────────────────────────────────────────────

def encode_he_text(text: str) -> bytes:
    """Encode a Hebrew/mixed string to the game's custom single-byte encoding."""
    segments = _TOKEN_RE.split(text)

    if REVERSE_FOR_LTR:
        segments = segments[::-1]

    def _encode_chars(chars: list[str]) -> None:
        for ch in chars:
            if ch in HEBREW_TO_CODE:
                result.append(HEBREW_TO_CODE[ch])
            elif 0x20 <= ord(ch) <= 0x7E:
                result.append(ord(ch))
            elif ord(ch) == 0x0A:
                result.append(0x0A)
            elif ord(ch) != 0:
                _encode_warnings.append(ch)

    result = bytearray()
    for seg in segments:
        if _TOKEN_RE.fullmatch(seg):
            if seg.startswith('{'):
                # Game-engine code token — ASCII only, copy as-is
                for ch in seg:
                    code = ord(ch)
                    if 0x20 <= code <= 0x7E:
                        result.append(code)
            else:
                # Backtick-wrapped text — keep delimiters, encode inner content
                # normally so Hebrew characters are preserved
                result.append(ord('`'))
                inner = list(seg[1:-1])
                if REVERSE_FOR_LTR:
                    inner = inner[::-1]
                _encode_chars(inner)
                result.append(ord('`'))
        else:
            chars = list(seg)
            if REVERSE_FOR_LTR:
                chars = chars[::-1]
            _encode_chars(chars)

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


# ── Binary helpers ───────────────────────────────────────────────────────────

def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def scan_strings(data: bytes, start: int) -> list[tuple[int, str, bytes]]:
    """Return list of (offset, text, raw_bytes) for each null-terminated string."""
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
    """Parse strings into triplets [SOUND_ID, EN_TEXT, LANG_TEXT]."""
    triplets = []
    count = min(rc * 3, len(strings))
    for i in range(0, count - 2, 3):
        o0, s0, r0 = strings[i]
        o1, s1, r1 = strings[i + 1]
        o2, s2, r2 = strings[i + 2]
        triplets.append((o0, s0, r0, o1, s1, r1, o2, s2, r2))
    return triplets


# ── Hebrew sources ───────────────────────────────────────────────────────────

def _read_he_lines(path: str) -> list[str]:
    """Read a UTF-8 line-synced Hebrew file."""
    with open(path, encoding="utf-8") as f:
        # splitlines() handles LF / CRLF / lone-CR uniformly.
        return f.read().splitlines()


# ── Rebuild speech.info ───────────────────────────────────────────────────────

def rebuild_speech(he_lines: list[str]) -> tuple[int, int, int, list[str]]:
    """Rebuild speech.info using line N of he_lines for record N.

    RECORD_SIZE = 32 bytes (8 x uint32).
    Pointer formulas (verified):
      F6[i] = F5[i] + (SOUND_ID_len[i] - 3)
      F7[i] = F6[i] + (EN_TEXT_len[i] - 3)
      F5[i+1] = F5[i] + triplet_total_bytes[i] - 32
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

    if len(he_lines) < len(triplets):
        print(f"  WARNING: he.speech.txt has only {len(he_lines)} lines but "
              f"{len(triplets)} triplets — extra records will fall back to English.")

    # Phase 1: rebuild data section
    new_data = bytearray()
    translated = fallback = empty_count = 0
    missing_strings: list[str] = []
    triplet_meta: list[tuple[int, int, int]] = []  # (snd_len, en_len, total_bytes)

    for i, (o0, s0, r0, o1, s1, r1, o2, s2, r2) in enumerate(triplets):
        sound_bytes = r0 + b"\x00"
        en_bytes = r1 + b"\x00"

        if not s1:
            encoded = b""
            empty_count += 1
        else:
            he_raw = he_lines[i] if i < len(he_lines) else ""
            he_text = _unescape_newlines(he_raw)
            if _is_english_fallback(he_text, s1):
                encoded = reverse_ascii_bytes(r1) if not REVERSE_FOR_LTR else r1
                fallback += 1
                missing_strings.append(s1)
            else:
                encoded = encode_he_text(he_text)
                translated += 1

        he_bytes = encoded + b"\x00"

        new_data += sound_bytes + en_bytes + he_bytes
        triplet_meta.append((len(r0), len(r1),
                             len(sound_bytes) + len(en_bytes) + len(he_bytes)))

    # Trailing data after last triplet (extra strings beyond rc*3)
    if triplets:
        last_trip_end = triplets[-1][6] + len(triplets[-1][8]) + 1
        if last_trip_end < len(data):
            new_data += data[last_trip_end:]

    print(f"  Translated: {translated}, English fallback: {fallback}, Empty: {empty_count}")

    # Phase 2: recompute F5/F6/F7 via the recurrence relation
    orig_f5_0 = struct.unpack_from("<I", index_bytes, 5 * 4)[0]
    current_f5 = orig_f5_0
    pointer_updates = 0

    for i in range(rc):
        if i >= len(triplet_meta):
            break
        base = i * RECORD_SIZE
        snd_len, en_len, new_trip_bytes = triplet_meta[i]

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

        current_f5 = new_f5 + new_trip_bytes - RECORD_SIZE

    print(f"  Pointer records updated: {pointer_updates} / {rc}")

    # Phase 3: assemble final file
    new_file = pack_u32(rc) + bytes(index_bytes) + bytes(new_data)
    orig_size = len(data)
    new_size = len(new_file)
    print(f"  Original size: {orig_size:,} bytes -> New size: {new_size:,} bytes "
          f"(delta: {new_size - orig_size:+,})")

    if not DRY_RUN:
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback, empty_count, missing_strings


# ── Rebuild uitext.info ───────────────────────────────────────────────────────

def rebuild_uitext(he_lines: list[str]) -> tuple[int, int, list[str]]:
    """Rebuild uitext.info using line N of he_lines for record N.

    RECORD_SIZE = 8 bytes (2 × uint32 per record).
    Pointer encoding is SELF-RELATIVE:
      target_file_offset = stored_value + field_file_position
    """
    path = os.path.join(LOC_DIR, f"{TARGET_LANG}.uitext.info")
    with open(path, "rb") as f:
        data = f.read()

    rc = u32(data, 0)
    data_start = 4 + rc * UITEXT_RECORD_SIZE

    strings = scan_strings(data, data_start)

    pairs = []
    pair_count = min(rc, len(strings) // 2)
    for i in range(pair_count):
        o_key, key, r_key   = strings[i * 2]
        o_disp, disp, r_disp = strings[i * 2 + 1]
        pairs.append((o_key, key, r_key, o_disp, disp, r_disp))

    print(f"\n{TARGET_LANG}.uitext.info: {rc} records, {len(pairs)} KEY/DISPLAY pairs")

    if len(he_lines) < len(pairs):
        print(f"  WARNING: he.uitext.txt has only {len(he_lines)} lines but "
              f"{len(pairs)} pairs — extra records will fall back to English.")

    # Phase 1: rebuild data section
    new_data = bytearray()
    new_pair_offsets: list[tuple[int, int]] = []
    translated = fallback = 0
    missing_strings: list[str] = []

    for i, (o_key, key, r_key, o_disp, disp, r_disp) in enumerate(pairs):
        # KEY: keep verbatim
        key_off_in_new = len(new_data)
        new_data += r_key + b"\x00"

        if not disp:
            encoded = b""
        else:
            he_raw = he_lines[i] if i < len(he_lines) else ""
            he_text = _unescape_newlines(he_raw)
            if _is_english_fallback(he_text, disp):
                encoded = reverse_ascii_bytes(r_disp) if not REVERSE_FOR_LTR else r_disp
                fallback += 1
                missing_strings.append(disp)
            else:
                encoded = encode_he_text(he_text)
                translated += 1

        disp_off_in_new = len(new_data)
        new_data += encoded + b"\x00"

        new_pair_offsets.append((key_off_in_new, disp_off_in_new))

    if pairs:
        last_pair = pairs[-1]
        last_string_end = last_pair[3] + len(last_pair[5]) + 1
        if last_string_end < len(data):
            new_data += data[last_string_end:]

    print(f"  Translated: {translated}, English fallback: {fallback}")

    # Phase 2: recompute self-relative pointers
    new_index = bytearray(rc * UITEXT_RECORD_SIZE)
    for i in range(rc):
        if i < len(new_pair_offsets):
            key_off, disp_off = new_pair_offsets[i]
            key_abs  = data_start + key_off
            disp_abs = data_start + disp_off
        else:
            key_abs = disp_abs = 0

        f0_pos = 4 + i * UITEXT_RECORD_SIZE + 0
        f1_pos = 4 + i * UITEXT_RECORD_SIZE + 4
        f0_val = (key_abs  - f0_pos) & 0xFFFFFFFF
        f1_val = (disp_abs - f1_pos) & 0xFFFFFFFF
        struct.pack_into("<I", new_index, i * UITEXT_RECORD_SIZE + 0, f0_val)
        struct.pack_into("<I", new_index, i * UITEXT_RECORD_SIZE + 4, f1_val)

    # Phase 3: assemble final file
    new_file = pack_u32(rc) + bytes(new_index) + bytes(new_data)
    orig_size = len(data)
    new_size = len(new_file)
    print(f"  Original size: {orig_size:,} bytes -> New size: {new_size:,} bytes "
          f"(delta: {new_size - orig_size:+,})")

    if not DRY_RUN:
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback, missing_strings


# ── Backup restore ────────────────────────────────────────────────────────────

def restore_from_bak() -> None:
    """Restore .info files from their .bak counterparts (factory originals).

    Both .bak files must exist before any processing begins — they are the
    original factory files and must never be overwritten.  Exits with an error
    if either is missing.
    """
    files = [
        f"{TARGET_LANG}.speech.info",
        f"{TARGET_LANG}.uitext.info",
    ]
    missing = [
        os.path.join(LOC_DIR, f + ".bak")
        for f in files
        if not os.path.exists(os.path.join(LOC_DIR, f + ".bak"))
    ]
    if missing:
        print("ERROR: backup file(s) not found — cannot proceed without originals:")
        for p in missing:
            print(f"  {p}")
        print("Create backups of the original factory files first (copy to .bak).")
        sys.exit(1)

    if DRY_RUN:
        print("  [DRY RUN] Would restore .info files from .bak")
        return

    for f in files:
        src = os.path.join(LOC_DIR, f + ".bak")
        dst = os.path.join(LOC_DIR, f)
        shutil.copy2(src, dst)
        print(f"  Restored: {dst}  (from {src})")


# ── Report ────────────────────────────────────────────────────────────────────

def _write_report(path: str, speech_missing: list[str], uitext_missing: list[str]) -> None:
    """Write English-fallback strings (records that need translation) to a report."""
    seen: set[str] = set()
    all_missing: list[tuple[str, str]] = []
    for label, items in (("speech", speech_missing), ("uitext", uitext_missing)):
        for en in items:
            if en not in seen:
                seen.add(en)
                all_missing.append((label, en))

    import pathlib
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write("Records that fell back to English (inject_translation.py)\n")
        f.write(f"He dir  : {HE_DIR}\n")
        f.write(f"Loc dir : {LOC_DIR}\n")
        f.write(f"Total missing (unique): {len(all_missing)}\n")
        f.write(f"  speech : {len(speech_missing)}\n")
        f.write(f"  uitext : {len(uitext_missing)}\n")
        f.write("=" * 70 + "\n")
        current_label = None
        for label, en in all_missing:
            if label != current_label:
                f.write(f"\n[{label.upper()}]\n")
                current_label = label
            safe = en.replace("\r\n", "\\n").replace("\r", "\\r").replace("\n", "\\n")
            f.write(f"{safe}\n")

    print(f"\n  Missing-translations report: {path}")
    print(f"  Total unique untranslated: {len(all_missing):,}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    he_speech_path = os.path.join(HE_DIR, "he.speech.txt")
    he_uitext_path = os.path.join(HE_DIR, "he.uitext.txt")
    missing_inputs = [p for p in (he_speech_path, he_uitext_path) if not os.path.exists(p)]
    if missing_inputs:
        print("ERROR: Hebrew source file(s) not found:")
        for m in missing_inputs:
            print(f"  {m}")
        print("Run extract_text.py + build_translation.py first to produce them.")
        return 1

    print(f"Loc dir : {LOC_DIR}")
    print(f"He dir  : {HE_DIR}")
    print(f"Encoding: REVERSE_FOR_LTR={REVERSE_FOR_LTR}")
    if DRY_RUN:
        print("*** DRY RUN MODE - no files will be written ***")
    print()

    print("Restoring .info files from backups...")
    restore_from_bak()
    print()

    print(f"Loading {he_speech_path} ...")
    he_speech_lines = _read_he_lines(he_speech_path)
    print(f"  {len(he_speech_lines):,} lines")

    print(f"Loading {he_uitext_path} ...")
    he_uitext_lines = _read_he_lines(he_uitext_path)
    print(f"  {len(he_uitext_lines):,} lines")
    print()

    s_trans, s_fall, s_empty, s_missing = rebuild_speech(he_speech_lines)
    u_trans, u_fall, u_missing = rebuild_uitext(he_uitext_lines)

    total_entries = s_trans + s_fall + s_empty
    pct = s_trans * 100 // total_entries if total_entries else 0

    print()
    print("=" * 60)
    print("INJECTION COMPLETE")
    print("=" * 60)
    print(f"  Speech: {s_trans:,} Hebrew, {s_fall:,} English fallback, {s_empty:,} empty")
    print(f"  UItext: {u_trans:,} Hebrew, {u_fall:,} English fallback")
    print(f"  Overall speech coverage: {pct}% Hebrew")

    if _encode_warnings:
        unique_unknown = sorted(set(_encode_warnings))
        print(f"\n  WARNING: {len(_encode_warnings)} characters skipped (no mapping).")
        print(f"  Unique unknown chars: {unique_unknown}")
        print(f"  Add them to HEBREW_TO_CODE in scripts/fonts/hebrew_mapping.py if needed.")

    if _args.report:
        _write_report(_args.report, s_missing, u_missing)

    if not DRY_RUN:
        print()
        print("NEXT STEP: Repack the modified files into monkey2.pak")

    return 0


if __name__ == "__main__":
    sys.exit(main())

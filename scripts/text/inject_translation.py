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
_parser.add_argument(
    "--extra-mapping",
    nargs="+",
    default=[os.path.join(BASE_DIR, "translations", "extra_mapping.txt")],
    metavar="FILE",
    help="One or more secondary mapping files tried as exact-match fallback, "
         "in order (first file takes priority on duplicate keys). "
         "(default: translations/extra_mapping.txt)",
)
_parser.add_argument(
    "--report",
    nargs="?",
    const=os.path.join(BASE_DIR, "translations", "missing_translations.txt"),
    default=None,
    metavar="FILE",
    help="Write a report of untranslated strings to FILE "
         "(default path: translations/missing_translations.txt)",
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

RECORD_SIZE = 32        # speech.info: 8 × uint32 per record
UITEXT_RECORD_SIZE = 8  # uitext.info: 2 × uint32 per record (KEY, DISPLAY_TEXT)

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




# ── Translation lookup ────────────────────────────────────────────────────────

_MULTI_SPACE = re.compile(r' {2,}')
# Matches characters that are neither standard ASCII printable (0x20-0x7E) nor
# Hebrew Unicode block (U+05D0–U+05FF) — used to strip game-engine noise chars.
_SPECIAL_CHAR_RE = re.compile(r'[^\x20-\x7E\u05D0-\u05FF]')

# Populated by load_mapping():
#   _nospace_index       key.replace(' ', '')        -> hebrew  (case-sensitive)
#   _nospace_lower_index key.replace(' ', '').lower() -> hebrew  (case-insensitive)
_nospace_index: dict[str, str] = {}
_nospace_lower_index: dict[str, str] = {}

# Loaded from the extra mapping file; queried only with exact-match as last resort.
_extra_table: dict[str, str] = {}


def _strip_special(key: str) -> str:
    """Replace non-ASCII-printable / non-Hebrew chars with a space, then normalise."""
    return _MULTI_SPACE.sub(' ', _SPECIAL_CHAR_RE.sub(' ', key)).strip()


def _toggle_period(key: str) -> str:
    """Return key with trailing period removed if present, or added if absent."""
    return key[:-1] if key.endswith('.') else key + '.'


def _simple_lookup_no_swap(table: dict[str, str], key: str) -> str | None:
    """Steps 1-8: space/case/period lookups — no quote-swap to avoid recursion."""
    # 1. Exact match
    if key in table and table[key]:
        return table[key]
    # 2. Period-toggled exact key
    toggled = _toggle_period(key)
    if toggled in table and table[toggled]:
        return table[toggled]
    # 3. lstrip (whitespace)
    stripped = key.lstrip()
    if stripped != key and stripped in table and table[stripped]:
        return table[stripped]
    # 4. lstrip + period-toggled
    toggled_stripped = _toggle_period(stripped)
    if stripped != key and toggled_stripped in table and table[toggled_stripped]:
        return table[toggled_stripped]
    # 4b. Strip leading quote/apostrophe/backtick characters (e.g. "'--gag--" → "--gag--")
    quote_stripped = stripped.lstrip("'`\"")
    if quote_stripped != stripped:
        if quote_stripped in table and table[quote_stripped]:
            return table[quote_stripped]
        toggled_qs = _toggle_period(quote_stripped)
        if toggled_qs in table and table[toggled_qs]:
            return table[toggled_qs]
    # 5. Collapse multi-spaces
    normalized = _MULTI_SPACE.sub(' ', key).strip()
    if normalized != key and normalized != stripped and normalized in table and table[normalized]:
        return table[normalized]
    # 6. Collapse multi-spaces + period-toggled
    toggled_normalized = _toggle_period(normalized)
    if normalized != key and normalized != stripped and toggled_normalized in table and table[toggled_normalized]:
        return table[toggled_normalized]
    # 6b. Strip special characters (non-ASCII-printable, non-Hebrew), re-normalise
    clean = _strip_special(key)
    if clean != key and clean != normalized:
        if clean in table and table[clean]:
            return table[clean]
        toggled_clean = _toggle_period(clean)
        if toggled_clean in table and table[toggled_clean]:
            return table[toggled_clean]
    # 7. Remove all spaces (case-sensitive)
    nospace = key.replace(' ', '')
    if nospace in _nospace_index:
        return _nospace_index[nospace]
    # 8. Remove all spaces + case-insensitive
    nospace_lower = nospace.lower()
    if nospace_lower in _nospace_lower_index:
        return _nospace_lower_index[nospace_lower]
    return None


def _simple_lookup(table: dict[str, str], key: str) -> str | None:
    """Steps 1-10: all direct lookups including quote-swap variants."""
    result = _simple_lookup_no_swap(table, key)
    if result:
        return result
    # 9. Swap backtick → single-quote (game uses ` as apostrophe in some strings)
    if '`' in key:
        result = _simple_lookup_no_swap(table, key.replace('`', "'"))
        if result:
            return result
    # 10. Swap single-quote → backtick
    if "'" in key:
        result = _simple_lookup_no_swap(table, key.replace("'", '`'))
        if result:
            return result
    return None


# Matches leading non-word chars used as separators between compound parts,
# e.g. the ". " in  "`Title`. Some comment."
_LEADING_SEP_RE = re.compile(r'^[^\w`{(]+')


def _resolve_token(table: dict[str, str], token: str) -> str | None:
    """Try to find the Hebrew for a backtick-wrapped token.

    Tries the full token (with backticks), then the inner content (without),
    re-wrapping the result in backticks if found via the inner lookup.
    """
    he = _simple_lookup(table, token)
    if he:
        return he
    inner = token[1:-1]  # strip surrounding backticks
    he_inner = _simple_lookup(table, inner)
    if he_inner:
        return '`' + he_inner + '`'
    return None


def _resolve_other(table: dict[str, str], other: str) -> tuple[str, str] | None:
    """Try to find the Hebrew for the non-token (plain-text) side.

    Strips whitespace and leading separators before looking up.
    Returns (chosen_candidate, hebrew) or None.
    """
    other_stripped = other.strip()
    if not other_stripped:
        return None
    candidates = [other_stripped]
    stripped_punct = _LEADING_SEP_RE.sub('', other_stripped)
    if stripped_punct and stripped_punct != other_stripped:
        candidates.append(stripped_punct)
    for candidate in candidates:
        he = _simple_lookup(table, candidate)
        if he:
            return candidate, he
    return None


def _try_split(table: dict[str, str], key: str,
               token: str, prefix: str, suffix: str) -> str | None:
    """Given a specific (token, prefix, suffix) split, attempt compound translation."""
    he_token = _resolve_token(table, token)
    if not he_token:
        return None

    # Case A: backtick first, plain text after
    if suffix.strip():
        result = _resolve_other(table, suffix)
        if result:
            chosen_msg, he_other = result
            token_end = key.find(token) + len(token)
            msg_pos = key.find(chosen_msg, token_end)
            connector = key[token_end:msg_pos] if msg_pos >= 0 else ''
            return prefix + he_token + connector + he_other

    # Case B: plain text first, backtick last
    if prefix.strip():
        result = _resolve_other(table, prefix)
        if result:
            chosen_msg, he_other = result
            msg_pos = key.find(chosen_msg)
            msg_end = msg_pos + len(chosen_msg) if msg_pos >= 0 else len(prefix)
            token_pos = key.find(token)
            connector = key[msg_end:token_pos] if token_pos >= 0 else ''
            return he_other + connector + he_token

    return None


def _compound_lookup(table: dict[str, str], key: str) -> str | None:
    """Step 9: handle messages composed of a backtick part + a plain-text part.

    Supports both orderings (backtick-first, plain-first) and two tokenisations:
      - Shortest match: `I Am Blackbeard` in `I Am Blackbeard`s Liver.`
      - Greedy match:   `I Am Blackbeard`s Liver.`  (first ` to last `)
        Needed when a backtick inside the token is used as an apostrophe.
    """
    # ── Shortest-match tokenisation (normal) ─────────────────────────────────
    parts = _TOKEN_RE.split(key)
    token_indices = [
        i for i, p in enumerate(parts)
        if _TOKEN_RE.fullmatch(p) and p.startswith('`')
    ]
    if token_indices:
        ti = token_indices[0]
        token  = parts[ti]
        prefix = ''.join(parts[:ti])
        suffix = ''.join(parts[ti + 1:])
        result = _try_split(table, key, token, prefix, suffix)
        if result:
            return result

    # ── Greedy tokenisation: first ` to last ` ────────────────────────────────
    # Handles "`I Am Blackbeard`s Liver.`" where ` inside is an apostrophe.
    first_bt = key.find('`')
    last_bt  = key.rfind('`')
    if first_bt >= 0 and last_bt > first_bt:
        greedy_token  = key[first_bt:last_bt + 1]
        greedy_prefix = key[:first_bt]
        greedy_suffix = key[last_bt + 1:]
        # Only try if this differs from the shortest-match split
        if not token_indices or greedy_token != parts[token_indices[0]]:
            result = _try_split(table, key, greedy_token, greedy_prefix, greedy_suffix)
            if result:
                return result

    return None


def _var_token_lookup(table: dict[str, str], key: str) -> str | None:
    """Step 10: handle messages that contain {game_code} tokens mixed with plain text.

    Example:  "I must be off now.{var:global_26}"
      plain text  = "I must be off now."
      token       = "{var:global_26}"
      result      = Hebrew("I must be off now.") + "{var:global_26}"

    Tokens before the text are prepended; tokens after are appended.
    Tokens interspersed within the text are kept at the end (best-effort).
    """
    parts = _TOKEN_RE.split(key)
    curly_indices = {
        i for i, p in enumerate(parts)
        if _TOKEN_RE.fullmatch(p) and p.startswith('{')
    }
    if not curly_indices:
        return None

    text_only = ''.join(p for i, p in enumerate(parts) if i not in curly_indices)
    if not text_only.strip():
        return None

    he = _simple_lookup(table, text_only)
    if not he:
        return None

    # Determine the span occupied by plain-text parts
    text_indices = {i for i, p in enumerate(parts) if i not in curly_indices and p}
    if not text_indices:
        return None
    text_start = min(text_indices)
    text_end   = max(text_indices)

    prefix = ''.join(parts[i] for i in sorted(curly_indices) if i < text_start)
    middle = ''.join(parts[i] for i in sorted(curly_indices) if text_start <= i <= text_end)
    suffix = ''.join(parts[i] for i in sorted(curly_indices) if i > text_end)

    return prefix + he + middle + suffix


def lookup(table: dict[str, str], key: str) -> str | None:
    """Return the Hebrew translation for key using a cascade of fallbacks.

    1.  Exact match.
    2.  Period-toggled exact key (add/remove trailing '.').
    3.  lstrip.
    4.  lstrip + period-toggled.
    5.  Collapse multi-spaces.
    6.  Collapse multi-spaces + period-toggled.
    7.  Remove ALL spaces (case-sensitive).
    8.  Remove ALL spaces + case-insensitive.
    9.  Quote-swap (backtick ↔ single-quote) applied to steps 1-8.
    10. Compound backtick split: translate "`Title`" and plain text separately.
        Handles "`Title`. Some comment." and "Some comment. `Title`".
    11. {game_code} token strip: remove {tokens}, translate plain text,
        reattach tokens at their original positions.
        Handles "I must be off now.{var:global_26}".
    12. Strip all backticks and retry steps 1-9.
        Handles "`Dead ahead!`" -> look up "Dead ahead!".
    """
    result = _simple_lookup(table, key)
    if result:
        return result
    result = _compound_lookup(table, key)
    if result:
        return result
    result = _var_token_lookup(table, key)
    if result:
        return result
    # 12. Strip all backticks and retry
    if '`' in key:
        stripped = key.replace('`', '')
        if stripped.strip():
            result = _simple_lookup(table, stripped)
            if result:
                return result
    # 13. Fallback in the extra mapping file.
    #     Tries multiple key variants so the same normalisation rules that
    #     apply to the main table also work against the extra mapping:
    #       a) exact key
    #       b) special-char-stripped key (handles non-breaking spaces etc.)
    #       c) backtick-stripped key (handles `wrapped` strings)
    #       d) backtick-stripped + special-char-stripped
    def _extra(k: str) -> str | None:
        return _extra_table.get(k) or None

    if _extra(key):
        return _extra_table[key]
    clean_key = _strip_special(key)
    if clean_key != key and _extra(clean_key):
        return _extra_table[clean_key]
    if '`' in key:
        bt_key = key.replace('`', '')
        if "E Ticket" in bt_key:
            print(f"E Ticket in bt_key: {key}")
            print(f"E Ticket in bt_key: {bt_key}")
            print(f"E Ticket in bt_key: {_extra(bt_key)}")
        if bt_key.strip() and _extra(bt_key):
            return _extra_table[bt_key]
        clean_bt = _strip_special(bt_key)
        if clean_bt != bt_key and _extra(clean_bt):
            return _extra_table[clean_bt]
    return None


# ── Rebuild speech.info ───────────────────────────────────────────────────────

def rebuild_speech(table: dict[str, str]) -> tuple[int, int, int, list[str]]:
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
    missing_strings: list[str] = []

    # Per triplet: (snd_len, en_len, new_triplet_total_bytes)
    triplet_meta: list[tuple[int, int, int]] = []

    for o0, s0, r0, o1, s1, r1, o2, s2, r2 in triplets:
        sound_bytes = r0 + b"\x00"
        en_bytes = r1 + b"\x00"

        he_text = lookup(table, s1)
        if he_text:
            encoded = encode_he_text(he_text)
            translated += 1
        elif s1:
            encoded = reverse_ascii_bytes(r1) if not REVERSE_FOR_LTR else r1
            fallback += 1
            missing_strings.append(s1)
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
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback, empty_count, missing_strings


# ── Rebuild uitext.info ───────────────────────────────────────────────────────

def rebuild_uitext(table: dict[str, str]) -> tuple[int, int, list[str]]:
    """Rebuild uitext.info — kept as in-place patch (no truncation issue here).

    RECORD_SIZE = 8 bytes (2 × uint32 per record).
    Each record stores two string pointers:
      F0 -> KEY string  (internal engine ID, e.g. 'MENU_PLAY_NEW_GAME') — never translated
      F1 -> DISPLAY string (human-readable text, e.g. 'PLAY NEW GAME')   — translated

    Translation lookup is performed on the DISPLAY string (F1), not the KEY (F0).
    Only the DISPLAY slot is patched in-place; the KEY slot is left untouched.
    """
    path = os.path.join(LOC_DIR, f"{TARGET_LANG}.uitext.info")
    with open(path, "rb") as f:
        data = f.read()

    rc = u32(data, 0)
    data_start = 4 + rc * UITEXT_RECORD_SIZE

    strings = scan_strings(data, data_start)

    # Pairs: stride-2 (KEY, DISPLAY_TEXT)
    pairs = []
    for i in range(0, len(strings) - 1, 2):
        o_key, key, r_key   = strings[i]
        o_disp, disp, r_disp = strings[i + 1]
        pairs.append((o_key, key, r_key, o_disp, disp, r_disp))

    print(f"\n{TARGET_LANG}.uitext.info: {rc} records, {len(pairs)} KEY/DISPLAY pairs")

    # In-place patch: overwrite only the DISPLAY slot (null-pad or truncate)
    new_data = bytearray(data[data_start:])
    translated = fallback = 0
    missing_strings: list[str] = []

    for o_key, key, r_key, o_disp, disp, r_disp in pairs:
        old_len = len(r_disp) + 1
        section_off = o_disp - data_start

        # Look up the DISPLAY text (the actual English string), not the KEY
        he_text = lookup(table, disp)
        if he_text:
            encoded = encode_he_text(he_text)
            translated += 1
        else:
            encoded = reverse_ascii_bytes(r_disp) if not REVERSE_FOR_LTR else r_disp
            fallback += 1
            if disp:
                missing_strings.append(disp)

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
        with open(path, "wb") as f:
            f.write(new_file)
        print(f"  Written: {path}")
    else:
        print(f"  [DRY RUN] Would write {path}")

    return translated, fallback, missing_strings


# ── Backup restore ────────────────────────────────────────────────────────────

def restore_from_bak() -> None:
    """Restore .info files from their .bak counterparts.

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


# ── Main ──────────────────────────────────────────────────────────────────────

def load_mapping(mapping_path: str) -> dict[str, str]:
    """Load a Windows-1255 mapping file (english === hebrew) into a dict.

    Also populates the module-level _nospace_index (key-with-no-spaces -> he)
    used as the last-resort fallback in lookup().
    """
    global _nospace_index, _nospace_lower_index
    table: dict[str, str] = {}
    nospace: dict[str, str] = {}
    nospace_lower: dict[str, str] = {}
    with open(mapping_path, encoding="windows-1255", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip("\r\n")
            if " === " not in line:
                continue
            en, _, he = line.partition(" === ")
            # Do NOT strip en — leading/trailing spaces are part of the key and
            # must match the exact text stored in the game's .info binary.
            he = he.strip()
            if en:
                table[en] = he
                ns_key = en.replace(' ', '')
                if ns_key:
                    if ns_key not in nospace:
                        nospace[ns_key] = he
                    ns_lower = ns_key.lower()
                    if ns_lower not in nospace_lower:
                        nospace_lower[ns_lower] = he
    _nospace_index = nospace
    _nospace_lower_index = nospace_lower
    return table


def _write_report(path: str, speech_missing: list[str], uitext_missing: list[str]) -> None:
    """Write untranslated English strings to a UTF-8 report file."""
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
        f.write("Missing translations report\n")
        f.write(f"Mapping : {_args.mapping_file}\n")
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


def main():
    mapping_path = _args.mapping_file
    if not os.path.exists(mapping_path):
        print(f"ERROR: mapping file not found: {mapping_path}")
        return

    print("Restoring .info files from backups...")
    restore_from_bak()

    table = load_mapping(mapping_path)

    global _extra_table
    _extra_table = {}
    for extra_path in _args.extra_mapping:
        if not os.path.exists(extra_path):
            print(f"Extra mapping not found, skipping: {extra_path}")
            continue
        loaded = 0
        with open(extra_path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if " === " not in line:
                    continue
                en, _, he = line.partition(" === ")
                he = he.strip()
                # Decode escape sequences so keys with literal \n / \r match
                # the actual newline bytes stored in the game's .info files.
                #en = en.replace("\\n", "\n").replace("\\r", "\r")
                if en and en not in _extra_table:   # first file wins
                    _extra_table[en] = he
                    loaded += 1
        print(f"Extra mapping loaded: {loaded} entries  ({extra_path})")

    print(f"Translation table loaded: {len(table)} entries")
    print(f"Encoding: REVERSE_FOR_LTR={REVERSE_FOR_LTR}")
    if DRY_RUN:
        print("*** DRY RUN MODE - no files will be written ***\n")

    s_trans, s_fall, s_empty, s_missing = rebuild_speech(table)
    u_trans, u_fall, u_missing = rebuild_uitext(table)

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

    if _args.report:
        _write_report(_args.report, s_missing, u_missing)

    if not DRY_RUN:
        print()
        print("NEXT STEP: Repack the modified files into monkey2.pak")


if __name__ == "__main__":
    main()

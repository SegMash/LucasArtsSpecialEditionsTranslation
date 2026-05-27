"""
Extract ordered, user-visible English text from the game's .info files.

Reads:
  <loc_dir>/<lang>.speech.info   -> the EN_TEXT field of every triplet
  <loc_dir>/<lang>.uitext.info   -> the DISPLAY field of every KEY/DISPLAY pair

Writes (one line per record, preserving the original record order):
  <out_dir>/<lang>.speech.txt
  <out_dir>/<lang>.uitext.txt

The output files are line-synced sources for translation:
  - line N in en.speech.txt is the English text of record N in en.speech.info
  - line N in en.uitext.txt is the DISPLAY text of record N in en.uitext.info

Newlines inside a message (\\n, \\r) are escaped to literal "\\n" / "\\r"
so each message occupies exactly ONE line in the output file.  When the
matching Hebrew file is later produced by `build_translation.py`, line N
in the Hebrew file translates the message on line N of the English file.

Encoding: the .info files store ASCII bytes for English text, so the output
is written as UTF-8 (compatible).  Hebrew is NOT involved in this step.

Usage:
    python extract_text.py [<loc_dir>] [-o <out_dir>] [--lang <lang>]

Arguments:
    loc_dir      Directory containing the <lang>.speech.info / <lang>.uitext.info
                 files.  Default: quickbms/output/localization
    -o, --out    Directory to write the .txt files into.
                 Default: translations
    --lang       Language prefix of the .info files.  Default: en
"""

import argparse
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fix_argv() -> None:
    """Work around the PowerShell trailing-backslash quoting bug.

    When a quoted path ends with a backslash (e.g. "C:\\path\\dir\\"),
    PowerShell treats the \\" as an escaped quote and merges the rest of the
    command line into that one argument.  We detect the embedded " and
    re-split.
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


# ── .info parsing helpers ────────────────────────────────────────────────────

RECORD_SIZE_SPEECH = 32  # speech.info: 8 × uint32 per record
RECORD_SIZE_UITEXT = 8   # uitext.info: 2 × uint32 per record (KEY, DISPLAY)


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _scan_strings(data: bytes, start: int) -> list[tuple[int, str, bytes]]:
    """Return list of (offset, decoded_text, raw_bytes) for every null-terminated
    string starting at `start`.  Decoded with UTF-8 first, falling back to
    latin-1 (matches the strategy used in inject_translation.py)."""
    results: list[tuple[int, str, bytes]] = []
    pos, n = start, len(data)
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


def _escape_newlines(text: str) -> str:
    """Convert real newline / carriage-return characters to literal "\\n"/"\\r"
    so each message occupies a single line in the output file."""
    # Order matters: handle CRLF first so we don't double-escape.
    return (text
            .replace("\r\n", "\\n")
            .replace("\r",   "\\r")
            .replace("\n",   "\\n"))


# ── Extractors ───────────────────────────────────────────────────────────────

def extract_speech(info_path: str) -> list[str]:
    """Return the EN_TEXT (middle string of each triplet) for every record."""
    with open(info_path, "rb") as f:
        data = f.read()

    rc = _u32(data, 0)
    data_start = 4 + rc * RECORD_SIZE_SPEECH
    strings = _scan_strings(data, data_start)

    triplet_count = min(rc, len(strings) // 3)
    out: list[str] = []
    for i in range(triplet_count):
        # strings[3*i + 0] = SOUND_ID
        # strings[3*i + 1] = EN_TEXT     <- this is what we want
        # strings[3*i + 2] = LANG_TEXT
        en_text = strings[3 * i + 1][1]
        out.append(_escape_newlines(en_text))

    print(f"  {os.path.basename(info_path)}: {rc} records, "
          f"{triplet_count} triplets extracted")
    return out


def extract_uitext(info_path: str) -> list[str]:
    """Return the DISPLAY string (second string of each KEY/DISPLAY pair)."""
    with open(info_path, "rb") as f:
        data = f.read()

    rc = _u32(data, 0)
    data_start = 4 + rc * RECORD_SIZE_UITEXT
    strings = _scan_strings(data, data_start)

    pair_count = min(rc, len(strings) // 2)
    out: list[str] = []
    for i in range(pair_count):
        # strings[2*i + 0] = KEY
        # strings[2*i + 1] = DISPLAY    <- this is what we want
        disp = strings[2 * i + 1][1]
        out.append(_escape_newlines(disp))

    print(f"  {os.path.basename(info_path)}: {rc} records, "
          f"{pair_count} KEY/DISPLAY pairs extracted")
    return out


# ── Writer ───────────────────────────────────────────────────────────────────

def _write_lines(out_path: str, lines: list[str]) -> None:
    """Write each entry on its own line; UTF-8, LF line endings."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(ln)
            f.write("\n")
    print(f"  -> {out_path}  ({len(lines)} lines)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract ordered English text from .info files into line-synced .txt files."
    )
    parser.add_argument(
        "loc_dir",
        nargs="?",
        default=os.path.join(BASE_DIR, "quickbms", "output", "localization"),
        help="Directory containing the <lang>.speech.info / <lang>.uitext.info files "
             "(default: quickbms/output/localization)",
    )
    parser.add_argument(
        "-o", "--out",
        dest="out_dir",
        default=os.path.join(BASE_DIR, "translations"),
        help="Output directory for the .txt files (default: translations)",
    )
    parser.add_argument(
        "--lang",
        default="en",
        help="Language prefix of the .info files (default: en)",
    )
    args = parser.parse_args()

    loc_dir = args.loc_dir.rstrip("\\/")
    out_dir = args.out_dir.rstrip("\\/")

    speech_in = os.path.join(loc_dir, f"{args.lang}.speech.info")
    uitext_in = os.path.join(loc_dir, f"{args.lang}.uitext.info")

    missing = [p for p in (speech_in, uitext_in) if not os.path.exists(p)]
    if missing:
        print("ERROR: input file(s) not found:")
        for p in missing:
            print(f"  {p}")
        return 1

    print(f"Loc dir : {loc_dir}")
    print(f"Out dir : {out_dir}")
    print(f"Lang    : {args.lang}")
    print()
    print("Extracting speech.info ...")
    speech_lines = extract_speech(speech_in)
    print()
    print("Extracting uitext.info ...")
    uitext_lines = extract_uitext(uitext_in)
    print()
    print("Writing .txt files ...")
    _write_lines(os.path.join(out_dir, f"{args.lang}.speech.txt"), speech_lines)
    _write_lines(os.path.join(out_dir, f"{args.lang}.uitext.txt"), uitext_lines)
    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Build the line-synced Hebrew translation files from the ordered English
extracts produced by `extract_text.py` and the existing mapping files.

For each English line:
  - Look up the Hebrew translation via the full fallback cascade
    (translation_lookup.TranslationLookup).
  - If found, write the Hebrew text on the matching line of the output file.
  - If NOT found, write the English line verbatim — keeping line N synced.

Newlines (\\n / \\r) inside a message are kept ESCAPED in both the input and
output .txt files: each message always occupies exactly one line.

Output files are NOT overwritten by default — pass --override to replace,
or --merge to refresh ONLY the English-fallback lines (i.e. preserve any
hand-edits already in the target Hebrew file).

Usage:
    python build_translation.py [-i <translations_dir>] [-o <translations_dir>]
                                [--mapping FILE] [--extra-mapping FILE [FILE ...]]
                                [--lang en] [--override | --merge]

Default I/O:
  Inputs  : translations/en.speech.txt
            translations/en.uitext.txt
            translations/mapping.txt                (Windows-1255, primary)
            translations/*_mapping.txt              (UTF-8, all extras
                                                     auto-discovered, sorted
                                                     alphabetically — first file
                                                     wins on duplicate keys)
  Outputs : translations/he.speech.txt
            translations/he.uitext.txt
            translations/missing_from_build.txt   (only if --report)
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local import (sibling module).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from translation_lookup import TranslationLookup  # noqa: E402


def _fix_argv() -> None:
    """Work around the PowerShell trailing-backslash quoting bug."""
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


# ── Newline escape/unescape ──────────────────────────────────────────────────
# extract_text.py escapes real \n / \r in messages to literal "\\n" / "\\r"
# so each message stays on a single line.  We must:
#   - UNESCAPE before lookup, because the mapping keys contain real newlines
#     (matching what's stored byte-for-byte in the .info files).
#   - RE-ESCAPE the Hebrew result before writing, so the synced .txt file
#     keeps its one-message-per-line invariant.

def unescape_newlines(text: str) -> str:
    """Convert literal \\n / \\r back to real newline / carriage-return chars."""
    return text.replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\r", "\r")


def escape_newlines(text: str) -> str:
    """Convert real newline / CR characters to literal \\n / \\r."""
    return (text
            .replace("\r\n", "\\n")
            .replace("\r",   "\\r")
            .replace("\n",   "\\n"))


# ── File helpers ─────────────────────────────────────────────────────────────

def _read_lines(path: str) -> list[str]:
    """Read a UTF-8 text file as a list of lines (newline at EOL stripped)."""
    with open(path, encoding="utf-8") as f:
        # splitlines() handles LF / CRLF / lone-CR equivalently.
        return f.read().splitlines()


def _write_lines(path: str, lines: list[str]) -> None:
    """Write each entry on its own line, UTF-8, LF endings, parent dirs created."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(ln)
            f.write("\n")


# ── Build pass ───────────────────────────────────────────────────────────────

def build_one(name: str,
              src_path: str,
              dst_path: str,
              lookup: TranslationLookup,
              merge: bool = False) -> tuple[int, int, int]:
    """Process one English file, write the matching Hebrew file.

    When `merge` is True and `dst_path` already exists, any line in the target
    that differs from the corresponding English line is treated as a hand-edit
    and preserved verbatim.  Lines that are still English fallback are
    re-looked-up via the cascade (so newly-added mapping entries pick them up).

    Returns (translated_count, fallback_count, preserved_edits).  In non-merge
    mode `preserved_edits` is always 0.
    """
    en_lines = _read_lines(src_path)

    existing: list[str] = []
    if merge and os.path.exists(dst_path):
        existing = _read_lines(dst_path)

    out_lines: list[str] = []
    translated = 0
    fallback   = 0
    preserved  = 0

    for i, en_escaped in enumerate(en_lines):
        if not en_escaped:
            out_lines.append("")
            continue
        # Preserve hand-edits in merge mode (line index matches between EN and HE).
        if merge and i < len(existing):
            cur = existing[i]
            if cur and cur != en_escaped:
                out_lines.append(cur)
                translated += 1
                preserved  += 1
                continue
        en_real = unescape_newlines(en_escaped)
        he = lookup.lookup(en_real)
        if he:
            out_lines.append(escape_newlines(he))
            translated += 1
        else:
            out_lines.append(en_escaped)
            fallback += 1

    _write_lines(dst_path, out_lines)
    msg = (f"  {name}: {translated} translated, {fallback} English fallback "
           f"({len(en_lines)} lines)")
    if merge:
        msg += f", {preserved} hand-edits preserved"
    msg += f"  ->  {dst_path}"
    print(msg)
    return translated, fallback, preserved


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Build line-synced Hebrew translation files from English "
                    "extracts + mapping files."
    )
    p.add_argument(
        "-i", "--in-dir",
        dest="in_dir",
        default=os.path.join(BASE_DIR, "translations"),
        help="Directory containing the <lang>.speech.txt / <lang>.uitext.txt "
             "files (default: translations)",
    )
    p.add_argument(
        "-o", "--out-dir",
        dest="out_dir",
        default=os.path.join(BASE_DIR, "translations"),
        help="Directory to write he.speech.txt / he.uitext.txt into "
             "(default: translations)",
    )
    p.add_argument(
        "--mapping",
        default=os.path.join(BASE_DIR, "translations", "mapping.txt"),
        help="Primary mapping file, Windows-1255, lines: 'english === hebrew' "
             "(default: translations/mapping.txt)",
    )
    p.add_argument(
        "--extra-mapping",
        nargs="+",
        default=None,
        metavar="FILE",
        help="One or more secondary UTF-8 mapping files tried after the primary "
             "(first file takes priority on duplicate keys). "
             "Default: every translations/*_mapping.txt file, sorted "
             "alphabetically (so e.g. extra_mapping.txt < uit_text_mapping.txt). "
             "Pass this flag to override the auto-discovery with an explicit list.",
    )
    p.add_argument(
        "--lang",
        default="en",
        help="Source language prefix (default: en)",
    )
    p.add_argument(
        "--target-lang",
        default="he",
        help="Output language prefix (default: he)",
    )
    write_mode = p.add_mutually_exclusive_group()
    write_mode.add_argument(
        "--override",
        action="store_true",
        help="Overwrite existing output files completely (default: refuse if they exist).",
    )
    write_mode.add_argument(
        "--merge",
        action="store_true",
        help="Refresh the existing output files in place, preserving any line "
             "that already differs from the matching English source line "
             "(treated as a hand-edit).  English-fallback lines are re-looked-up "
             "with the current mapping tables.",
    )
    p.add_argument(
        "--report",
        nargs="?",
        const=os.path.join(BASE_DIR, "translations", "missing_from_build.txt"),
        default=None,
        metavar="FILE",
        help="Write a report of untranslated lines to FILE "
             "(default path: translations/missing_from_build.txt)",
    )
    args = p.parse_args()

    in_dir  = args.in_dir.rstrip("\\/")
    out_dir = args.out_dir.rstrip("\\/")

    # ── Auto-discover extra mapping files when --extra-mapping is not given ──
    # All files matching translations/*_mapping.txt are loaded in alphabetical
    # order.  The primary 'mapping.txt' is excluded by the underscore in the
    # glob.  Pass --extra-mapping explicitly to override this behaviour.
    if args.extra_mapping is None:
        translations_dir = os.path.dirname(os.path.abspath(args.mapping))
        discovered = sorted(glob.glob(os.path.join(translations_dir,
                                                   "*_mapping.txt")))
        args.extra_mapping = discovered

    src_speech = os.path.join(in_dir, f"{args.lang}.speech.txt")
    src_uitext = os.path.join(in_dir, f"{args.lang}.uitext.txt")
    dst_speech = os.path.join(out_dir, f"{args.target_lang}.speech.txt")
    dst_uitext = os.path.join(out_dir, f"{args.target_lang}.uitext.txt")

    # ── Validate inputs ─────────────────────────────────────────────────────
    missing_inputs = [p for p in (src_speech, src_uitext) if not os.path.exists(p)]
    if missing_inputs:
        print("ERROR: input file(s) not found:")
        for m in missing_inputs:
            print(f"  {m}")
        print("Run extract_text.py first to produce the English .txt files.")
        return 1

    if not os.path.exists(args.mapping):
        print(f"ERROR: mapping file not found: {args.mapping}")
        return 1

    # ── Validate outputs / override / merge ─────────────────────────────────
    existing_outputs = [p for p in (dst_speech, dst_uitext) if os.path.exists(p)]
    if existing_outputs and not (args.override or args.merge):
        print("ERROR: output file(s) already exist "
              "(pass --override to replace, or --merge to preserve hand-edits):")
        for e in existing_outputs:
            print(f"  {e}")
        return 1

    # ── Load tables ─────────────────────────────────────────────────────────
    print(f"In dir  : {in_dir}")
    print(f"Out dir : {out_dir}")
    print(f"Mapping : {args.mapping}")
    if args.extra_mapping:
        print(f"Extra mappings ({len(args.extra_mapping)}, in load order):")
        for ep in args.extra_mapping:
            print(f"  - {ep}")
    else:
        print("Extra mappings : (none)")
    print(f"Source language : {args.lang}")
    print(f"Target language : {args.target_lang}")
    print()

    lookup = TranslationLookup()
    n = lookup.load_mapping(args.mapping)
    print(f"Primary mapping loaded : {n:,} entries  ({args.mapping})")

    for extra_path in args.extra_mapping:
        if not os.path.exists(extra_path):
            print(f"Extra mapping not found, skipping: {extra_path}")
            continue
        m = lookup.load_extra_mapping(extra_path)
        print(f"Extra mapping loaded   : {m:,} entries  ({extra_path})")

    print()
    if args.merge:
        print("Building Hebrew files (merge mode — hand-edits preserved) ...")
    else:
        print("Building Hebrew files ...")

    s_trans, s_fall, s_pres = build_one(
        "speech", src_speech, dst_speech, lookup, merge=args.merge)
    u_trans, u_fall, u_pres = build_one(
        "uitext", src_uitext, dst_uitext, lookup, merge=args.merge)

    total_lines  = s_trans + s_fall + u_trans + u_fall
    total_trans  = s_trans + u_trans
    pct = total_trans * 100 // total_lines if total_lines else 0

    print()
    print("=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    print(f"  Speech : {s_trans:,} translated, {s_fall:,} English fallback"
          + (f", {s_pres:,} hand-edits preserved" if args.merge else ""))
    print(f"  Uitext : {u_trans:,} translated, {u_fall:,} English fallback"
          + (f", {u_pres:,} hand-edits preserved" if args.merge else ""))
    print(f"  Overall coverage: {pct}% Hebrew  ({total_trans:,} / {total_lines:,})")

    # ── Optional missing-translations report ────────────────────────────────
    if args.report:
        missing: list[tuple[str, str]] = []
        for label, src_path in (("speech", src_speech), ("uitext", src_uitext)):
            for ln in _read_lines(src_path):
                if not ln:
                    continue
                if not lookup.lookup(unescape_newlines(ln)):
                    missing.append((label, ln))

        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8", errors="replace") as f:
            f.write("Missing translations report (build_translation.py)\n")
            f.write(f"Mapping        : {args.mapping}\n")
            f.write(f"Extra mapping  : {', '.join(args.extra_mapping)}\n")
            f.write(f"Total missing  : {len(missing)}\n")
            f.write("=" * 70 + "\n")
            current_label = None
            for label, ln in missing:
                if label != current_label:
                    f.write(f"\n[{label.upper()}]\n")
                    current_label = label
                f.write(f"{ln}\n")
        print(f"  Missing-translations report: {args.report}")
        print(f"  ({len(missing):,} untranslated lines)")

    print()
    print("NEXT STEP: hand-edit the new he.*.txt files for context-specific")
    print("translations (e.g. \"Excuse me.\" -> 'סלח לי' / 'סלחו לי' / 'סליחה'),")
    print("then run inject_translation.py to write the Hebrew .info files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

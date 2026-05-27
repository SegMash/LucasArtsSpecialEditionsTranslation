"""
build_hebrew_font.py — Full pipeline to add Hebrew support to game fonts.

Runs four steps for each font:
  1. parse_font.py             — extract glyph PNGs + glyph_manifest.csv
  2. generate_hebrew_glyphs.py — render Hebrew letters (overwrite old slots)
  3. rebuild_manifest.py       — recompute x_left / x_right / width in manifest
  4. create_font.py            — pack into a new .font + atlas .png

Two modes
---------
Single font:
    python build_hebrew_font.py <fonts_dir> <font_name>

All fonts in folder:
    python build_hebrew_font.py <fonts_dir>

Options
-------
    --output-dir DIR   Where to write the rebuilt .font + .png files.
                       Default: same as fonts_dir (overwrites originals).
    --ttf PATH         Hebrew TTF for regular / italic fonts.
                       Default: C:\\Windows\\Fonts\\frank.ttf
    --ttf-bold PATH    Hebrew TTF for bold / bold-oblique fonts.
                       Default: C:\\Windows\\Fonts\\frank.ttf  (FRANKB.TTF
                       renders too thick at small cell heights; reusing the
                       regular face here looks better in-game.)
    --original-dir DIR Folder with the untouched original .font files used as
                       templates. Default: same as fonts_dir.
    --max-fraction N   Max Hebrew letter height as fraction of cell height.
                       Default: 0.70
    --align MODE       'center' (default) or 'left'.  Use 'left' to place every
                       Hebrew letter starting at col 0, exactly like the original
                       Latin glyphs — recommended when the game hangs on startup.
    --border-mode M    For border fonts (name contains _bo_) where to draw the
                       1-pixel black contour: 'outer' (default — halo around the
                       letter) or 'inner' (paint outermost letter pixels black).
    --clean            Delete the glyph subfolder after each font is built.
    --dry-run          Print what would be done without executing anything.

Font-variant auto-detection
---------------------------
    Name contains _b_ or _bo_  → bold TTF (--ttf-bold; default frank.ttf)
    Everything else             → regular TTF (--ttf;       default frank.ttf)
"""

import sys
import os
import shutil
import subprocess
import time
import argparse
import glob

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_TTF      = r"C:\Windows\Fonts\frank.ttf"
DEFAULT_TTF_BOLD = r"C:\Windows\Fonts\frank.ttf"

STEPS = [
    ("parse_font.py",             "Extract glyphs + manifest"),
    ("generate_hebrew_glyphs.py", "Render Hebrew letters    "),
    ("rebuild_manifest.py",       "Rebuild manifest layout  "),
    ("create_font.py",            "Pack .font + atlas PNG   "),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def choose_ttf(font_name: str, args) -> str:
    """Pick the right TTF based on font name variant."""
    name = font_name.lower()
    if "_bo_" in name or "_b_" in name:
        return args.ttf_bold
    return args.ttf


def run_step(script: str, extra_args: list[str]) -> tuple[bool, float, str]:
    """Run one pipeline step. Returns (success, elapsed_s, stderr_tail)."""
    cmd = [sys.executable, "-X", "utf8",
           os.path.join(SCRIPTS_DIR, script)] + extra_args
    t0  = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    elapsed = time.time() - t0
    ok      = res.returncode == 0
    # Keep last 3 lines of stderr for error reporting
    stderr_tail = "\n".join(res.stderr.strip().splitlines()[-3:]) if res.stderr.strip() else ""
    return ok, elapsed, stderr_tail


def progress_bar(current: int, total: int, width: int = 44) -> None:
    """Print an in-place progress bar (call with current == total to finish)."""
    if total == 0:
        return
    filled = int(width * current / total)
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(100 * current / total)
    end    = "\n" if current == total else ""
    print(f"\r  [{bar}] {pct:3d}%  {current}/{total} fonts", end=end, flush=True)


def fmt_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


# ── Per-font pipeline ─────────────────────────────────────────────────────────

def process_font(font_name: str, fonts_dir: str, output_dir: str,
                 args) -> tuple[bool, str]:
    """
    Run all four steps for one font.
    Returns (success, error_message).
    """
    # Source .font file to parse glyphs from (may already be Hebrew-rebuilt).
    font_file = os.path.join(fonts_dir, f"{font_name}.font")
    glyph_dir = os.path.join(fonts_dir,  font_name)
    ttf       = choose_ttf(font_name, args)

    # Template .font file for create_font: MUST be the original unmodified file
    # so that the header, char-table, and non-position metric bytes are preserved
    # verbatim.  If --original-dir is given, use that; otherwise fall back to
    # fonts_dir (works correctly on a first run against pristine original fonts).
    orig_dir      = getattr(args, "original_dir", None) or fonts_dir
    template_file = os.path.join(orig_dir, f"{font_name}.font")

    if not os.path.isfile(font_file):
        return False, f".font file not found: {font_file}"
    if not os.path.isfile(template_file):
        return False, f"original .font template not found: {template_file}"

    step_args = [
        # 1. parse_font
        [font_file],
        # 2. generate_hebrew_glyphs
        [glyph_dir, "--font", ttf, "--max-fraction", str(args.max_fraction),
         "--align", args.align, "--border-mode", args.border_mode]
        + (["--test-line"] if args.test_line else [])
        + (["--no-quantize"] if not args.quantize else []),
        # 3. rebuild_manifest — preserve-positions mode (default):
        #    only updates bearing_x / advance_x for changed Hebrew glyphs;
        #    x_left / x_right of ALL glyphs stay at original parsed positions.
        #    This prevents non-Hebrew glyph positions from shifting, which can
        #    cause the game engine to misinterpret the atlas layout.
        [glyph_dir, "--hebrew-gap", str(args.hebrew_gap)],
        # 4. create_font — NO --repack: use preserve-original mode.
        #    x_right of Hebrew glyphs is auto-adjusted to match their PNG widths;
        #    non-Hebrew glyph positions are byte-identical to the original font.
        [glyph_dir, output_dir,
         "--template", template_file],
    ]

    total_elapsed = 0.0
    for step_num, ((script, label), extra) in enumerate(zip(STEPS, step_args), 1):
        print(f"    [{step_num}/4] {label}  ", end="", flush=True)

        if args.dry_run:
            print("(dry-run)")
            continue

        ok, elapsed, stderr = run_step(script, extra)
        total_elapsed += elapsed

        if ok:
            print(f"✓  {elapsed:.1f}s")
        else:
            print(f"✗  FAILED  ({elapsed:.1f}s)")
            if stderr:
                for line in stderr.splitlines():
                    print(f"           ! {line}")
            return False, f"Step {step_num} ({label.strip()}) failed"

    if getattr(args, "clean", False) and os.path.isdir(glyph_dir):
        if not args.dry_run:
            shutil.rmtree(glyph_dir)
        print(f"    [clean] Removed glyph folder: {glyph_dir}")

    return True, fmt_time(total_elapsed)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Hebrew-enabled fonts for Monkey Island 2 SE.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("fonts_dir",
                        help="Folder containing the .font files.")
    parser.add_argument("font_name", nargs="?", default=None,
                        help="Process only this font (e.g. MinisterT_24). "
                             "Omit to process ALL fonts in fonts_dir.")
    parser.add_argument("--output-dir", default=None, metavar="DIR",
                        help="Destination for rebuilt .font + .png files. "
                             "Default: same as fonts_dir.")
    parser.add_argument("--original-dir", default=None, metavar="DIR",
                        help="Folder containing the untouched original .font files "
                             "used as binary templates (header + char-table preserved). "
                             "Default: same as fonts_dir. Specify this when fonts_dir "
                             "already contains previously rebuilt Hebrew fonts.")
    parser.add_argument("--ttf", default=DEFAULT_TTF, metavar="PATH",
                        help=f"Hebrew TTF for regular/italic fonts. Default: {DEFAULT_TTF}")
    parser.add_argument("--ttf-bold", default=DEFAULT_TTF_BOLD, metavar="PATH",
                        help=f"Hebrew TTF for bold/bold-oblique fonts. Default: {DEFAULT_TTF_BOLD}")
    parser.add_argument("--max-fraction", type=float, default=0.70, metavar="N",
                        help="Max Hebrew letter height as fraction of cell height (default: 0.70).")
    parser.add_argument("--hebrew-gap", type=int, default=1, metavar="N",
                        help="Extra pixels of advance after each Hebrew glyph (default: 1). "
                             "advance_x = letter_width + hebrew_gap. "
                             "Increase for more space between letters.")
    parser.add_argument("--align", choices=["left", "center"], default="left",
                        help="Letter placement within its glyph slot. "
                             "'left' (default): pixels start at column 0, matching the "
                             "original atlas convention.  PNG width = natural letter width, "
                             "so rebuild_manifest correctly updates advance_x per letter. "
                             "'center': centre letter in original slot width — PNG width "
                             "stays the same, so advance_x is NOT recalculated.")
    parser.add_argument("--border-mode", choices=["outer", "inner"], default="outer",
                        help="For border fonts (font name contains '_bo_'), where the "
                             "1-pixel black contour is painted. "
                             "'outer' (default): paint the transparent pixels touching the "
                             "letter — letter body stays bright, a 1-px halo is added. "
                             "'inner': paint the outermost letter pixels black — same "
                             "footprint, slightly thinner letter body. "
                             "Ignored for non-border fonts.")
    parser.add_argument("--test-line", action="store_true",
                        help="DIAGNOSTIC: draw a white horizontal line at mid-height of every "
                             "glyph (x=0..width-2) to test whether the game requires a pixel "
                             "near the right edge of each slot.")
    parser.add_argument("--no-quantize", dest="quantize", action="store_false",
                        help="Disable the default pixel quantization.  By default every "
                             "Hebrew glyph pixel is snapped to the nearest of the 5 RGBA "
                             "levels found in the original font atlas (faint/dark/mid/light/"
                             "solid) to match the colour encoding the game engine expects.")
    parser.set_defaults(quantize=True)
    parser.add_argument("--clean", action="store_true",
                        help="Delete the glyph subfolder created during parsing "
                             "once the font is successfully built.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without running anything.")
    args = parser.parse_args()

    fonts_dir    = os.path.abspath(args.fonts_dir)
    output_dir   = os.path.abspath(args.output_dir)   if args.output_dir   else fonts_dir
    original_dir = os.path.abspath(args.original_dir) if args.original_dir else fonts_dir
    args.original_dir = original_dir   # make available inside process_font

    # ── Validate ──────────────────────────────────────────────────────────────
    if not os.path.isdir(fonts_dir):
        print(f"ERROR: fonts_dir not found: {fonts_dir}")
        sys.exit(1)
    if not os.path.isfile(args.ttf):
        print(f"ERROR: TTF font not found: {args.ttf}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # ── Collect fonts to process ──────────────────────────────────────────────
    if args.font_name:
        fonts = [args.font_name]
    else:
        font_files = sorted(glob.glob(os.path.join(fonts_dir, "*.font")))
        fonts = [os.path.splitext(os.path.basename(f))[0] for f in font_files]
        if not fonts:
            print(f"ERROR: no .font files found in {fonts_dir}")
            sys.exit(1)

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║         Hebrew Font Builder — Monkey Island 2 SE     ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  Fonts dir  : {fonts_dir}")
    print(f"  Originals  : {original_dir}")
    print(f"  Output dir : {output_dir}")
    print(f"  TTF regular: {args.ttf}")
    print(f"  TTF bold   : {args.ttf_bold}")
    print(f"  Max height : {args.max_fraction*100:.0f}% of cell")
    print(f"  Border mode: {args.border_mode}  (applies to *_bo_* fonts only)")
    if args.dry_run:
        print("  *** DRY RUN — no files will be written ***")
    print()

    # ── Single font mode ──────────────────────────────────────────────────────
    if len(fonts) == 1:
        font_name = fonts[0]
        ttf = choose_ttf(font_name, args)
        print(f"Processing: {font_name}  (TTF: {os.path.basename(ttf)})")
        print()
        ok, result = process_font(font_name, fonts_dir, output_dir, args)
        print()
        if ok:
            out_font = os.path.join(output_dir, f"{font_name}.font")
            out_png  = os.path.join(output_dir, f"{font_name}.png")
            print(f"  ✓  Done in {result}")
            print(f"     {out_font}")
            print(f"     {out_png}")
        else:
            print(f"  ✗  FAILED: {result}")
            sys.exit(1)

    # ── All-fonts mode ────────────────────────────────────────────────────────
    else:
        total     = len(fonts)
        succeeded = []
        failed    = []
        t_start   = time.time()

        print(f"  Found {total} fonts to process:")
        # Print font list in columns
        col_w = max(len(f) for f in fonts) + 2
        cols  = max(1, 72 // col_w)
        for i, f in enumerate(fonts):
            end = "\n" if (i + 1) % cols == 0 or i == total - 1 else ""
            print(f"  {f:<{col_w}}", end=end)
        print()

        for idx, font_name in enumerate(fonts, 1):
            ttf = choose_ttf(font_name, args)
            variant_tag = f"({os.path.basename(ttf)})"
            print(f"  [{idx:>2}/{total}] {font_name:<28} {variant_tag}")

            ok, result = process_font(font_name, fonts_dir, output_dir, args)

            if ok:
                succeeded.append(font_name)
                print(f"          ✓  {result}")
            else:
                failed.append((font_name, result))
                print(f"          ✗  {result}")

            # Progress bar after each font
            progress_bar(idx, total)
            print()

        # ── Summary ───────────────────────────────────────────────────────────
        total_time = time.time() - t_start
        print("══════════════════════════════════════════════════════")
        print(f"  Completed in {fmt_time(total_time)}")
        print(f"  ✓  {len(succeeded)}/{total} succeeded")
        if failed:
            print(f"  ✗  {len(failed)} failed:")
            for name, reason in failed:
                print(f"       • {name}: {reason}")
        print()
        print(f"  Output: {output_dir}")
        print("══════════════════════════════════════════════════════")

        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()

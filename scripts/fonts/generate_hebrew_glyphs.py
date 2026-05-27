"""
generate_hebrew_glyphs.backup.py — Render Hebrew letter glyphs as PNG files.
                                    (natural-width mode — metrics adapt per letter)

For each letter in HEBREW_TO_CODE, renders it with a TTF font and saves
the result as  <glyph_index>.png  in the target glyph directory.

Unlike the fixed-slot version, the PNG width equals the natural rendered
width of each letter, and rebuild_manifest.py then adapts the glyph metrics
(bearing_x, advance_x, x_left, x_right) to that actual width.

The font size is chosen automatically so that the tallest test letter (ץ)
occupies at most MAX_FRACTION of the cell height, leaving a small margin.
All letters share a common baseline so they look consistent together.

Usage
-----
    python generate_hebrew_glyphs.backup.py <glyph_dir> [options]

    glyph_dir   Folder that contains glyph_manifest.csv.
                The generated PNGs are written here (overwriting the
                matching European-character glyphs).

Options
-------
    --font PATH        TTF file to use.  Default: C:\\Windows\\Fonts\\frank.ttf
    --max-fraction N   Max letter height as a fraction of cell height
                       (0 < N < 1, default 0.70 = 70 %).
    --from N           First letter to generate (1 = Alef, 27 = Final Tsadi).
    --to N             Last letter to generate (inclusive).  Default: 27.
    --output-dir DIR   Write PNGs here instead of glyph_dir.
    --align MODE       left (default): letter starts at column 0, right edge
                       column is clamped to alpha=0.  Matches the original
                       atlas convention.
                       center: letter is centred inside the original glyph
                       slot width read from glyph_manifest.csv (fixed-width
                       output — metrics stay at original values).
    --no-quantize      Disable the default pixel quantization.  By default
                       every pixel is snapped to the nearest of the 5 RGBA
                       colour levels found in the original font atlas:
                       faint(3,3,3,1) / dark(128,128,128,64) /
                       mid(200,200,200,157) / light(240,240,240,226) /
                       solid(255,255,255,255).
    --border-mode M    For border fonts (output folder name contains "_bo_"),
                       choose where to draw the 1-pixel black contour:
                         outer (default): paint the transparent pixels touching
                                          the letter black — letter body stays
                                          bright, adds a 1-px halo (PNG is one
                                          column wider in natural-width mode).
                         inner          : paint the outermost letter pixels
                                          black — same footprint, thinner body.
"""

import sys
import os
import csv
import argparse

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Import the mapping defined in the same scripts/ folder ───────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hebrew_mapping import HEBREW_TO_CODE, _CODE_TO_GLYPH

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_FONT    = r"C:\Windows\Fonts\frank.ttf"
DEFAULT_FRACTION = 0.70     # tallest letter occupies at most 70 % of cell height
TEST_LETTER     = "ץ"       # use the letter with the longest descender as sizing reference


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size)


def _make_draw() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Return a large scratch canvas for measuring / rendering."""
    canvas = Image.new("RGBA", (2000, 2000), (0, 0, 0, 0))
    return canvas, ImageDraw.Draw(canvas)


def letter_extents(font: ImageFont.FreeTypeFont, letter: str) -> tuple[int, int]:
    """
    Return (ascent, descent) for *letter* relative to the font baseline.

    *ascent*  – pixels the glyph reaches ABOVE the baseline (positive).
    *descent* – pixels the glyph reaches BELOW the baseline (positive).

    Uses PIL's "ls" (left-baseline) anchor so coordinates are signed
    relative to the baseline at (0, 0).
    """
    _, draw = _make_draw()
    # anchor="ls": left-baseline → top is negative (above baseline),
    #                               bottom is positive (descenders)
    l, t, r, b = draw.textbbox((0, 0), letter, font=font, anchor="ls")
    ascent  = max(0, -t)   # how far above baseline
    descent = max(0,  b)   # how far below  baseline
    return ascent, descent


def rendered_height(letter: str, font: ImageFont.FreeTypeFont) -> int:
    """Total pixel height of *letter* (ascent + descent)."""
    a, d = letter_extents(font, letter)
    return a + d


def find_best_size(font_path: str, test_letter: str, cell_h: int, max_frac: float) -> int:
    """Binary-search for the largest font size where test_letter fits in max_frac * cell_h."""
    target = int(cell_h * max_frac)
    lo, hi, best = 4, cell_h * 3, 4
    while lo <= hi:
        mid  = (lo + hi) // 2
        font = load_font(font_path, mid)
        h    = rendered_height(test_letter, font)
        if h <= target:
            best = mid
            lo   = mid + 1
        else:
            hi   = mid - 1
    return best


def compute_baseline(font: ImageFont.FreeTypeFont,
                     cell_h: int) -> tuple[int, int, int]:
    """
    Measure ascent / descent across ALL Hebrew letters and compute a shared
    baseline_y (pixels from the top of the cell_h-tall image to the baseline).

    The resulting band (max_ascent + max_descent) is centred within cell_h.

    Returns (baseline_y, max_ascent, max_descent).
    """
    max_ascent = max_descent = 0
    for letter in HEBREW_TO_CODE:
        a, d = letter_extents(font, letter)
        max_ascent  = max(max_ascent,  a)
        max_descent = max(max_descent, d)

    band   = max_ascent + max_descent
    margin = max(0, (cell_h - band) // 2)
    return margin + max_ascent, max_ascent, max_descent


def render_letter(letter: str, font: ImageFont.FreeTypeFont,
                  cell_h: int, baseline_y: int) -> Image.Image:
    """
    Render *letter* white-on-transparent, exactly cell_h pixels tall.

    *baseline_y* is the shared baseline position (from the top of the image).
    Using a shared baseline means:
      - ל (Lamed, tall ascender) sits near the top
      - Regular body letters are centred on the baseline
      - ן / ך / ף / ץ (descenders) dip below the baseline

    Technique: draw with anchor="ls" on an oversized canvas, then extract
    the horizontal slice that belongs to this glyph while keeping the full
    cell_h vertical extent intact.
    """
    PAD = cell_h * 2          # generous vertical padding so nothing clips
    canvas = Image.new("RGBA", (font.size * 6, cell_h + PAD * 2), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    # Draw at x=0, y = PAD + baseline_y  (PAD pushes baseline away from top edge)
    draw.text((0, PAD + baseline_y), letter, font=font,
              fill=(255, 255, 255, 255), anchor="ls")

    # Find horizontal extent of the rendered pixels
    bbox = canvas.getbbox()
    if bbox is None:
        return Image.new("RGBA", (2, cell_h), (0, 0, 0, 0))

    l, _t, r, _b = bbox
    x_start = max(0, l - 1)          # 1-px left  padding
    x_end   = min(canvas.width, r + 1)  # 1-px right padding

    # Crop: full cell_h rows (starting from PAD = y=0 in the final image)
    strip = canvas.crop((x_start, PAD, x_end, PAD + cell_h))
    return strip


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render Hebrew glyphs as PNG files for use with create_font.py.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "glyph_dir",
        help=(
            "Folder with glyph_manifest.csv.  Hebrew PNGs are written here by default, "
            "overwriting the existing European-character glyph images."
        ),
    )
    parser.add_argument(
        "--font", default=DEFAULT_FONT,
        help=f"Path to TTF font file.  Default: {DEFAULT_FONT}",
    )
    parser.add_argument(
        "--max-fraction", type=float, default=DEFAULT_FRACTION, metavar="N",
        help=f"Max letter height as fraction of cell height (default {DEFAULT_FRACTION}).",
    )
    parser.add_argument(
        "--from", dest="letter_from", type=int, default=1, metavar="N",
        help="First letter to generate, 1-based (1 = Alef).  Default: 1.",
    )
    parser.add_argument(
        "--to", dest="letter_to", type=int, default=None, metavar="N",
        help="Last letter to generate, 1-based inclusive (27 = Final Tsadi).  Default: 27.",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Write PNGs to this directory instead of glyph_dir.",
    )
    parser.add_argument(
        "--align", choices=["left", "center"], default="left",
        help="How to place each letter in its PNG canvas.\n"
             "  left   (default): letter at column 0, right edge cleared.\n"
             "                    PNG width = natural rendered letter width.\n"
             "                    rebuild_manifest adapts metrics per letter.\n"
             "  center: letter centred inside the original slot width from\n"
             "          glyph_manifest.csv.  PNG width = original slot width.\n"
             "          Metrics stay at their original parsed values.",
    )
    parser.add_argument(
        "--no-quantize", dest="quantize", action="store_false",
        help="Disable the default pixel quantization.  By default every pixel\n"
             "is snapped to the nearest of the 5 RGBA levels from the original\n"
             "font atlas (faint/dark/mid/light/solid).",
    )
    parser.set_defaults(quantize=True)
    parser.add_argument(
        "--border-mode", choices=["inner", "outer"], default="outer",
        help="For border fonts (output folder name contains '_bo_'), where to\n"
             "paint the black 1-pixel contour:\n"
             "  outer (default): recolour the TRANSPARENT pixels adjacent to\n"
             "                   the letter black.  Letter body stays bright;\n"
             "                   adds a 1-pixel halo around it.  In the natural-\n"
             "                   width (left) mode an extra leading empty column\n"
             "                   is preserved so the left halo has room to\n"
             "                   appear, slightly widening each glyph PNG.\n"
             "  inner          : recolour the outermost LETTER pixels black.\n"
             "                   Footprint unchanged; original letter looks\n"
             "                   slightly thinner because the border eats into\n"
             "                   the glyph body.",
    )
    args = parser.parse_args()

    num_letters = len(HEBREW_TO_CODE)
    letter_from = max(1, args.letter_from)
    letter_to   = min(num_letters, args.letter_to if args.letter_to is not None else num_letters)
    if letter_from > letter_to:
        print(f"ERROR: --from {letter_from} > --to {letter_to}")
        sys.exit(1)

    out_dir = args.output_dir or args.glyph_dir
    os.makedirs(out_dir, exist_ok=True)

    # ── Read manifest (cell height + per-glyph slot widths for center mode) ───
    csv_path = os.path.join(args.glyph_dir, "glyph_manifest.csv")
    if not os.path.isfile(csv_path):
        print(f"ERROR: glyph_manifest.csv not found in {args.glyph_dir}")
        sys.exit(1)

    slot_widths: dict[int, int] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    first_row = rows[0]
    cell_h = int(first_row["height"])
    if args.align == "center":
        for row in rows:
            slot_widths[int(row["glyph_index"])] = int(row["width"])
    print(f"Cell height : {cell_h} px")

    # ── Find best font size ───────────────────────────────────────────────────
    font_path = args.font
    if not os.path.isfile(font_path):
        print(f"ERROR: font not found: {font_path}")
        sys.exit(1)

    font_size = find_best_size(font_path, TEST_LETTER, cell_h, args.max_fraction)
    font      = load_font(font_path, font_size)

    test_h = rendered_height(TEST_LETTER, font)
    print(f"Font size   : {font_size} pt")
    print(f"Test letter : '{TEST_LETTER}'  rendered height = {test_h} px  "
          f"(target ≤ {int(cell_h * args.max_fraction)} px  =  {args.max_fraction*100:.0f}% of {cell_h} px)")

    # ── Compute shared baseline across all Hebrew letters ────────────────────
    # baseline_y is the y-coordinate (within the cell_h image) of the font
    # baseline.  All letters are rendered relative to this shared line, so:
    #   - ל (tall ascender) sits near the top
    #   - regular body letters are on the baseline
    #   - ן / ך / ף / ץ (descenders) dip below
    baseline_y, max_ascent, max_descent = compute_baseline(font, cell_h)
    band = max_ascent + max_descent
    print(f"Baseline        : y={baseline_y}  "
          f"(ascent={max_ascent}, descent={max_descent}, "
          f"band={band}/{cell_h})")

    # ── Render each Hebrew letter ─────────────────────────────────────────────
    names = [
        "Alef","Bet","Gimel","Dalet","He","Vav","Zayin","Het","Tet","Yod",
        "Kaf","Lamed","Mem","Nun","Samekh","Ayin","Pe","Tsadi","Qof","Resh",
        "Shin","Tav","Final Kaf","Final Mem","Final Nun","Final Pe","Final Tsadi",
    ]

    all_letters = list(zip(HEBREW_TO_CODE.items(), names))   # [(letter,code), name]
    selected    = all_letters[letter_from - 1 : letter_to]   # slice is 0-based

    range_str = (f"{letter_from}–{letter_to}" if letter_from != letter_to
                 else str(letter_from))
    print(f"Generating letters {range_str} of {num_letters} "
          f"({len(selected)} glyph{'s' if len(selected) != 1 else ''})")
    print()
    print(f"{'#':<4} {'Letter':<6} {'Name':<14} {'Code':>5} {'Glyph':>6} {'Size':>10}  Path")
    print("-" * 74)

    # Palette used for --quantize (5 levels found in original font atlas).
    _PALETTE = np.array([
        (  3,   3,   3,   1),   # faint
        (128, 128, 128,  64),   # dark
        (200, 200, 200, 157),   # mid
        (240, 240, 240, 226),   # light
        (255, 255, 255, 255),   # solid
    ], dtype=np.int32)

    # Auto-detect border fonts: fonts whose name contains "_bo_" get a black
    # 1-pixel contour around each Hebrew glyph.  --border-mode controls whether
    # the contour is painted INSIDE the letter (default) or OUTSIDE it (test).
    _is_border_font = "_bo_" in os.path.basename(os.path.abspath(out_dir)).lower()
    _outer_border   = _is_border_font and args.border_mode == "outer"

    generated = 0
    for pos, ((letter, char_code), name) in enumerate(selected, start=letter_from):
        glyph_idx = _CODE_TO_GLYPH[char_code]
        raw_img   = render_letter(letter, font, cell_h, baseline_y)

        if args.align == "center":
            # ── Fixed-width mode: centre letter in the original slot canvas ──
            target_px = slot_widths.get(glyph_idx, raw_img.width)
            if raw_img.width <= target_px:
                img   = Image.new("RGBA", (target_px, cell_h), (0, 0, 0, 0))
                x_off = (target_px - raw_img.width) // 2
                img.paste(raw_img, (x_off, 0), raw_img)
            else:
                x_off = (raw_img.width - target_px) // 2
                img   = raw_img.crop((x_off, 0, x_off + target_px, cell_h))
            # Clamp both edge columns
            if target_px >= 3:
                arr = np.array(img)
                arr[:, 0,  3] = 0
                arr[:, -1, 3] = 0
                img = Image.fromarray(arr)
        else:
            # ── Natural-width mode (left): pixels start at column 0, exactly
            #    1 empty (transparent) column on the right — matching the
            #    original atlas convention.
            #    In outer-border mode we ALSO leave 1 empty column on the left
            #    so the black halo has room to paint there.
            arr = np.array(raw_img)
            col_max_a = arr[:, :, 3].max(axis=0)   # max alpha per column
            if col_max_a.any():
                first_col = int(np.argmax(col_max_a > 0))
                last_col  = int(np.where(col_max_a > 0)[0][-1])
                active_w  = last_col - first_col + 1
                leading_pad = 1 if _outer_border else 0
                new_w   = active_w + 1 + leading_pad   # active + 1 trailing + maybe 1 leading
                new_arr = np.zeros((arr.shape[0], new_w, 4), dtype=arr.dtype)
                new_arr[:, leading_pad : leading_pad + active_w, :] = arr[:, first_col : last_col + 1, :]
                # First column (when leading_pad) and last column stay transparent.
                img = Image.fromarray(new_arr)
            else:
                img = raw_img   # blank glyph — leave as-is

        # ── Quantize pixels to match original font's RGBA encoding ───────────
        if args.quantize:
            arr   = np.array(img, dtype=np.int32)
            alpha = arr[:, :, 3]
            mask  = alpha > 0
            if mask.any():
                px_a       = alpha[mask][:, None]          # N×1
                pal_a      = _PALETTE[:, 3][None, :]       # 1×5
                nearest    = np.argmin(np.abs(px_a - pal_a), axis=1)
                arr[mask]  = _PALETTE[nearest]
            img = Image.fromarray(arr.astype(np.uint8))

        # ── Black contour for _bo_ fonts (inner or outer per --border-mode) ──
        # Applied AFTER quantize so the solid-black colour is not snapped back
        # to white by the 5-level palette mapping.
        if _is_border_font:
            arr  = np.array(img, dtype=np.uint8)
            a    = arr[:, :, 3].astype(bool)   # True where letter pixel exists
            h, w = a.shape

            if args.border_mode == "outer":
                # Outer contour: paint TRANSPARENT pixels that touch a letter
                # pixel in any of the 4 cardinal directions.  Adds a 1-pixel
                # black halo around the letter; leaves the letter body bright.
                #
                # If the letter touches the image edge (no transparent room on
                # that side) the halo is clipped on that side — for the left
                # edge the natural-width trimming above already reserves a
                # leading empty column, so left/right are normally fine.
                adj_letter = np.zeros((h, w), dtype=bool)
                adj_letter[1:,  :] |= a[:-1, :]   # letter pixel above
                adj_letter[:-1, :] |= a[1:,  :]   # letter pixel below
                adj_letter[:,  1:] |= a[:,  :-1]  # letter pixel on the left
                adj_letter[:, :-1] |= a[:,  1:]   # letter pixel on the right
                border = (~a) & adj_letter
            else:
                # Inner contour (default): paint LETTER pixels that have at
                # least one transparent neighbour (or sit on the image edge).
                # The letter's footprint stays the same; the outline eats one
                # pixel into the glyph body.
                adj_transp = np.zeros((h, w), dtype=bool)
                adj_transp[0,  :]  = True          # top edge → above is outside
                adj_transp[-1, :]  = True          # bottom edge → below is outside
                adj_transp[:,  0]  = True          # left edge → left is outside
                adj_transp[:, -1]  = True          # right edge → right is outside
                adj_transp[1:,  :] |= ~a[:-1, :]   # above pixel is transparent
                adj_transp[:-1, :] |= ~a[1:,  :]   # below pixel is transparent
                adj_transp[:,  1:] |= ~a[:,  :-1]  # left pixel is transparent
                adj_transp[:, :-1] |= ~a[:,  1:]   # right pixel is transparent
                border = a & adj_transp

            arr[border] = [0, 0, 0, 255]       # solid black
            img = Image.fromarray(arr)

        out_path = os.path.join(out_dir, f"{glyph_idx:03d}.png")
        img.save(out_path)

        notes = []
        if args.align == "center":
            notes.append(f"centered in {target_px}px slot")
        if _is_border_font:
            notes.append(f"{args.border_mode}-border")
        if args.quantize:
            notes.append("quantized")
        note_str = f"  [{', '.join(notes)}]" if notes else ""
        print(f"  {pos:<3} {letter}     {name:<14} {char_code:>5} {glyph_idx:>6}   "
              f"{img.width:>3}×{cell_h:<3}px   {out_path}{note_str}")
        generated += 1

    print()
    print(f"Generated {generated} PNGs in: {os.path.abspath(out_dir)}")
    print()
    print("Next step: run create_font.py to pack these into the .font + atlas PNG.")


if __name__ == "__main__":
    main()

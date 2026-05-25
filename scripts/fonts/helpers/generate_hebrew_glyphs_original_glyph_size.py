"""
generate_hebrew_glyphs.py — Render Hebrew letter glyphs as PNG files.

For each letter in HEBREW_TO_CODE, renders it with a TTF font and saves
the result as  <glyph_index>.png  in the target glyph directory.

The font size is chosen automatically so that the tallest test letter (ץ)
occupies at most MAX_FRACTION of the cell height, leaving a small margin.
All letters share a common baseline so they look consistent together.

IMPORTANT: Each output PNG is forced to EXACTLY the same width as the
original glyph it replaces (width+1 pixels, where width = x_right - x_left
from the manifest).  This ensures the manifest and binary font file remain
100% identical to the original — only the atlas pixel content changes.
If the rendered letter is narrower it is centred with transparency padding;
if wider it is centred-cropped to fit.

Usage
-----
    python generate_hebrew_glyphs.py <glyph_dir> [options]

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
"""

import sys
import os
import csv
import argparse

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
        "--scale", type=float, default=1.0, metavar="N",
        help="Scale factor applied to the rendered letter before fitting into "
             "the target canvas (e.g. 0.5 = 50%% of rendered size, centred). "
             "Default: 1.0 (no scaling).",
    )
    parser.add_argument(
        "--shift", type=int, default=0, metavar="N",
        help="Horizontal pixel shift applied when centering the letter in the "
             "target canvas.  Negative = shift left, positive = shift right. "
             "Use -1 to nudge a right-leaning letter (e.g. ו) one pixel left "
             "so its stem does not fall on the last active column of the slot. "
             "Default: 0.",
    )
    parser.add_argument(
        "--align", choices=["left", "center"], default="center",
        help="How to place the rendered letter inside the target canvas.\n"
             "  center (default): centre the letter horizontally (original behaviour).\n"
             "  left            : place the letter starting at column 0, matching\n"
             "                    how every original glyph is laid out in the atlas\n"
             "                    (pixels at col 0 with no left-side transparent gap).\n"
             "                    The right edge column is still clamped to 0 to\n"
             "                    prevent anti-alias fringe from leaking rightward.",
    )
    parser.add_argument(
        "--test-line", action="store_true",
        help="DIAGNOSTIC: after rendering each glyph, draw a solid white horizontal\n"
             "line at y = cell_height // 2, spanning x = 0 .. width-2 (all columns\n"
             "except the rightmost).  This forces every glyph to have a pixel in the\n"
             "second-to-last column, matching the original font's right-edge convention.\n"
             "Use this to verify whether a full-width pixel row fixes a game hang.",
    )
    parser.add_argument(
        "--no-quantize", dest="quantize", action="store_false",
        help="Disable the default pixel quantization.  By default every pixel is\n"
             "snapped to the nearest of the 5 colour levels found in the original\n"
             "font atlas (faint/dark/mid/light/solid) to match the exact RGBA\n"
             "encoding the game engine expects.",
    )
    parser.set_defaults(quantize=True)
    args = parser.parse_args()

    num_letters = len(HEBREW_TO_CODE)
    letter_from = max(1, args.letter_from)
    letter_to   = min(num_letters, args.letter_to if args.letter_to is not None else num_letters)
    if letter_from > letter_to:
        print(f"ERROR: --from {letter_from} > --to {letter_to}")
        sys.exit(1)

    out_dir = args.output_dir or args.glyph_dir
    os.makedirs(out_dir, exist_ok=True)

    # ── Read manifest — cell height + per-glyph original widths ─────────────
    csv_path = os.path.join(args.glyph_dir, "glyph_manifest.csv")
    if not os.path.isfile(csv_path):
        print(f"ERROR: glyph_manifest.csv not found in {args.glyph_dir}")
        sys.exit(1)

    glyph_orig_width: dict[int, int] = {}   # glyph_index -> original stored width
    cell_h = None
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gidx  = int(row["glyph_index"])
            w     = int(row["width"])        # stored width = x_right - x_left
            glyph_orig_width[gidx] = w
            if cell_h is None:
                cell_h = int(row["height"])
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

    scale = args.scale
    if scale != 1.0:
        print(f"Scale factor: {scale*100:.0f}%  (letter shrunk before fitting into target canvas)")
    print()

    generated = 0
    for pos, ((letter, char_code), name) in enumerate(selected, start=letter_from):
        glyph_idx  = _CODE_TO_GLYPH[char_code]
        raw_img    = render_letter(letter, font, cell_h, baseline_y)

        # Optional: scale down the rendered letter
        if scale != 1.0:
            new_w = max(1, round(raw_img.width  * scale))
            new_h = max(1, round(raw_img.height * scale))
            scaled = raw_img.resize((new_w, new_h), Image.LANCZOS)
            # Re-centre vertically on a cell_h-tall transparent canvas
            raw_img = Image.new("RGBA", (new_w, cell_h), (0, 0, 0, 0))
            y_off   = (cell_h - new_h) // 2
            raw_img.paste(scaled, (0, y_off), scaled)

        # Force output to exactly (orig_stored_w + 1) pixels wide so the
        # manifest and binary font records stay byte-identical to the original.
        orig_w    = glyph_orig_width.get(glyph_idx)
        target_px = (orig_w + 1) if orig_w is not None else raw_img.width

        hshift = args.shift
        align  = args.align

        if align == "left":
            # ── Left-align: match the original atlas convention ──────────────────
            # Every original glyph in the font has its pixels starting at col 0.
            # Place the rendered letter at x=0 with no left-side padding.
            # If the rendered letter is wider than the slot, crop from the left.
            import numpy as _np
            if raw_img.width <= target_px:
                img = Image.new("RGBA", (target_px, cell_h), (0, 0, 0, 0))
                img.paste(raw_img, (0, 0), raw_img)
            else:
                img = raw_img.crop((0, 0, target_px, cell_h))
            # Only clamp the RIGHT edge to prevent anti-alias fringe leaking
            # into the slot of the NEXT glyph.  Left edge is left intact because
            # the letter content legitimately starts there.
            if target_px >= 2:
                arr = _np.array(img)
                arr[:, -1, 3] = 0   # right edge column → fully transparent
                img = Image.fromarray(arr)

        else:
            # ── Center-align (default) ────────────────────────────────────────────
            if raw_img.width == target_px:
                if hshift != 0:
                    import numpy as _np
                    arr = _np.array(raw_img)
                    arr = _np.roll(arr, hshift, axis=1)
                    if hshift > 0:
                        arr[:, :hshift, 3] = 0
                    else:
                        arr[:, hshift:, 3] = 0
                    img = Image.fromarray(arr)
                else:
                    img = raw_img
            elif raw_img.width < target_px:
                img   = Image.new("RGBA", (target_px, cell_h), (0, 0, 0, 0))
                x_off = (target_px - raw_img.width) // 2 + hshift
                x_off = max(0, min(x_off, target_px - raw_img.width))
                img.paste(raw_img, (x_off, 0), raw_img)
            else:
                x_off = (raw_img.width - target_px) // 2 - hshift
                x_off = max(0, min(x_off, raw_img.width - target_px))
                img   = raw_img.crop((x_off, 0, x_off + target_px, cell_h))

            # Safety: erase both edge columns to prevent LANCZOS anti-alias fringe
            # from leaking into adjacent glyph slots.
            if target_px >= 3:
                import numpy as _np
                arr = _np.array(img)
                arr[:, 0,  3] = 0   # left  edge → transparent
                arr[:, -1, 3] = 0   # right edge → transparent
                img = Image.fromarray(arr)

        # ── Optional diagnostic: white centre-line ────────────────────────────
        # Draws a solid white horizontal line at y = cell_h // 2 spanning
        # x = 0 .. target_px - 2 (all columns except the rightmost).
        # This tests whether the game engine requires a pixel within 1 column
        # of the slot's right edge.
        if args.test_line and target_px >= 2:
            import numpy as _np
            arr = _np.array(img)
            mid_y = cell_h // 2
            arr[mid_y, 0 : target_px - 1] = [255, 255, 255, 255]
            img = Image.fromarray(arr)

        # --quantize: snap every pixel to the nearest of the 5 original palette
        # entries (faint / dark / mid / light / solid).  Nearest is measured by
        # absolute distance in alpha, which is the dominant perceptual axis.
        if args.quantize:
            import numpy as _np
            # The 5 representative RGBA values found in the original font atlas.
            # Format: (R, G, B, A)
            _PALETTE = _np.array([
                (  3,   3,   3,   1),   # faint
                (128, 128, 128,  64),   # dark
                (200, 200, 200, 157),   # mid
                (240, 240, 240, 226),   # light
                (255, 255, 255, 255),   # solid
            ], dtype=_np.int32)

            arr = _np.array(img, dtype=_np.int32)   # H×W×4, int32 for signed math
            alpha = arr[:, :, 3]                    # H×W

            # For transparent pixels keep (0,0,0,0); only remap where alpha > 0.
            mask = alpha > 0
            if mask.any():
                # Distance = |pixel_alpha – palette_alpha| for each of 5 entries.
                px_a = alpha[mask][:, None]         # N×1
                pal_a = _PALETTE[:, 3][None, :]     # 1×5
                nearest_idx = _np.argmin(_np.abs(px_a - pal_a), axis=1)  # N
                mapped = _PALETTE[nearest_idx]      # N×4
                arr[mask] = mapped
            img = Image.fromarray(arr.astype(_np.uint8))

        out_path = os.path.join(out_dir, f"{glyph_idx:03d}.png")
        img.save(out_path)
        notes = []
        if scale != 1.0:
            notes.append(f"scaled to {scale*100:.0f}%")
        if orig_w is not None and raw_img.width != target_px:
            notes.append(f"canvas {raw_img.width}px→{target_px}px")
        if args.test_line:
            notes.append("test-line")
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

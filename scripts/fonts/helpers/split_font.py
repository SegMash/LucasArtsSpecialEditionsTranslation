"""
split_font.py - Split a Monkey Island 2 SE font PNG into individual letter images.

Usage:
    python split_font.py <path_to_font_png> [--min-gap N]

Arguments:
    path_to_font_png   Path to the font PNG file to split.
    --min-gap N        Minimum number of consecutive empty rows required to
                       treat a gap as a row separator. Default: auto (1 for
                       regular/bold, 3 for italic fonts whose names contain '_i_').

Example:
    python split_font.py "c:/GOG Games/Monkey Island 2 SE/quickbms/output/fonts/MinisterT_24.png"
    python split_font.py MinisterT_i_24.png --min-gap 4

Output:
    A sub-folder named after the PNG (without .png) is created next to the input file.
    Each letter is saved as 001.png, 002.png, ... in reading order (row by row, left to right).
    A letters.csv file is also written with columns:
        index, row, x_min, y_min, x_max, y_max, width, height

Image format notes:
    - 32-bit RGBA.  R=G=B=A (alpha doubles as luminance).
    - Fully white (255,255,255,255) = letter body.
    - Gray shades = anti-aliased border.
    - Transparent (alpha=0) = background.
"""

import sys
import os
import re
import csv
import argparse
import numpy as np
from PIL import Image
from scipy.ndimage import label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_line_height(filename: str) -> int:
    """Extract the line height from the filename, e.g. MinisterT_24.png -> 24."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    numbers = re.findall(r"\d+", stem)
    return int(numbers[-1]) if numbers else 0


def is_italic(filename: str) -> bool:
    """Return True if the font filename indicates an italic variant (_i_)."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    return "_i_" in stem or stem.endswith("_i")


def find_row_bands(
    alpha: np.ndarray,
    min_gap: int = 1,
) -> list[tuple[int, int]]:
    """
    Find the vertical extent (y_start, y_end inclusive) of each line of glyphs.

    Scans for runs of fully-transparent rows.  A run of at least `min_gap`
    consecutive empty rows is treated as a separator between bands.
    """
    row_has_content = np.any(alpha > 0, axis=1)
    height = len(row_has_content)

    bands: list[tuple[int, int]] = []
    in_band = False
    band_start = 0
    empty_run = 0

    for y in range(height):
        if row_has_content[y]:
            if not in_band:
                in_band = True
                band_start = y
            empty_run = 0
        else:
            if in_band:
                empty_run += 1
                if empty_run >= min_gap:
                    band_end = y - empty_run
                    bands.append((band_start, band_end))
                    in_band = False
                    empty_run = 0

    if in_band:
        bands.append((band_start, height - 1))

    return bands


def get_letters_in_band(
    img_array: np.ndarray,
    y_start: int,
    y_end: int,
) -> list[tuple[int, int, int, int]]:
    """
    Find individual letter bounding boxes (full-image coordinates) within a row band.

    Uses connected-component labeling on the alpha channel slice.
    Returns a list of (x_min, y_min, x_max, y_max), sorted left to right.
    """
    alpha_slice = img_array[y_start : y_end + 1, :, 3]
    binary = alpha_slice > 0
    labeled, num_features = label(binary)

    boxes: list[tuple[int, int, int, int]] = []
    for comp_id in range(1, num_features + 1):
        ys, xs = np.where(labeled == comp_id)
        if len(xs) == 0:
            continue
        pixel_count = len(xs)
        x_min, x_max = int(xs.min()), int(xs.max())
        y_min_rel = int(ys.min())
        y_max_rel = int(ys.max())
        y_min = y_start + y_min_rel
        y_max = y_start + y_max_rel

        # Skip single-pixel isolated noise
        w = x_max - x_min + 1
        h = y_max - y_min + 1
        if pixel_count < 3 and w < 2 and h < 2:
            continue

        boxes.append((x_min, y_min, x_max, y_max))

    boxes.sort(key=lambda b: b[0])
    return boxes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def split_font(png_path: str, min_gap: int | None = None) -> None:
    if not os.path.isfile(png_path):
        print(f"ERROR: File not found: {png_path}")
        sys.exit(1)

    stem = os.path.splitext(os.path.basename(png_path))[0]
    out_dir = os.path.join(os.path.dirname(png_path), stem)
    os.makedirs(out_dir, exist_ok=True)

    line_height = parse_line_height(png_path)
    print(f"Font: {stem}  |  line height from name: {line_height}px")

    # Auto-select min_gap if not supplied
    if min_gap is None:
        min_gap = 3 if is_italic(png_path) else 1
    print(f"Row-gap threshold: {min_gap} empty row(s)")

    img = Image.open(png_path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[:, :, 3]

    # Step 1: detect row bands
    bands = find_row_bands(alpha, min_gap=min_gap)
    print(f"\nRow bands found: {len(bands)}")
    for i, (y0, y1) in enumerate(bands):
        print(f"  Band {i + 1}: y={y0}..{y1}  (height={y1 - y0 + 1}px)")

    if not bands:
        print("No content found in image. Exiting.")
        return

    # Step 2: find individual letters within each band
    all_rows: list[list[tuple[int, int, int, int]]] = []
    for y0, y1 in bands:
        letters = get_letters_in_band(arr, y0, y1)
        if letters:
            all_rows.append(letters)

    total_letters = sum(len(r) for r in all_rows)
    print(f"\nTotal letter components detected: {total_letters}")
    for i, row in enumerate(all_rows):
        print(f"  Row {i + 1}: {len(row)} components")

    # Step 3: save crops + write CSV
    csv_path = os.path.join(out_dir, "letters.csv")
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["index", "row", "x_min", "y_min", "x_max", "y_max", "width", "height"])

        letter_index = 1
        for row_num, row in enumerate(all_rows, start=1):
            for x_min, y_min, x_max, y_max in row:
                pad = 1
                cx1 = max(0, x_min - pad)
                cy1 = max(0, y_min - pad)
                cx2 = min(img.width, x_max + pad + 1)
                cy2 = min(img.height, y_max + pad + 1)

                letter_img = img.crop((cx1, cy1, cx2, cy2))
                out_name = f"{letter_index:03d}.png"
                letter_img.save(os.path.join(out_dir, out_name))

                w = x_max - x_min + 1
                h = y_max - y_min + 1
                writer.writerow(
                    [letter_index, row_num, x_min, y_min, x_max, y_max, w, h]
                )
                letter_index += 1

    saved = letter_index - 1
    print(f"\nSaved {saved} letter images  ->  {out_dir}")
    print(f"CSV summary                ->  {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split a Monkey Island 2 SE font PNG into individual letter images."
    )
    parser.add_argument("png_path", help="Path to the font PNG file.")
    parser.add_argument(
        "--min-gap",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Minimum empty rows between bands (default: 1 for regular/bold, "
            "3 for italic fonts detected by '_i_' in the filename)."
        ),
    )
    args = parser.parse_args()
    split_font(args.png_path, min_gap=args.min_gap)

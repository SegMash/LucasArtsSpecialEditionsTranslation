"""
merge_dxt_chunks.py - Merge tiled DXT1/DXT5 chunk files into PNG image(s).

Game images are stored as one or more DXT tiles whose filenames encode both
an image id and the pixel offset of each tile:

    layer0_chunk_0_0.dxt        -> image "layer0" at (0, 0)
    layer0_chunk_1024_0.dxt     -> image "layer0" at (1024, 0)
    layer1_chunk_0_0.dxt        -> image "layer1" at (0, 0)

Any number of tiles per image is supported; the canvas is the bounding box of
the tiles that are present.  Multiple images in one directory each produce
their own PNG.  Standalone .dxt files (no "chunk" in the name) are decoded
directly to PNG without merging.

Each .dxt file has a 12-byte header:
    bytes 0-3:  magic "DXT1" or "DXT5"
    bytes 4-7:  width  (uint32 LE)
    bytes 8-11: height (uint32 LE)
followed by raw block data:
    DXT1/BC1:  8 bytes per 4x4 block
    DXT5/BC3: 16 bytes per 4x4 block

Usage:
    python merge_dxt_chunks.py images/en/96_part1
    python merge_dxt_chunks.py extracted/art              # all subdirectories
    python merge_dxt_chunks.py rooms/images

All PNGs are written flat under images/en/, with path parts joined by "__":
    rooms/images/10_logo/extra_fog_f0  ->  images/en/rooms__images__10_logo__extra_fog_f0.png
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

HEADER_SIZE = 12
BLOCK_DIM = 4
DXT1_BLOCK_SIZE = 8
DXT5_BLOCK_SIZE = 16

# Only "..._chunk_X_Y.dxt" tiles are used as merge groups; other .dxt files
# are decoded as standalone whole images.
CHUNK_RE = re.compile(
    r"^(?P<name>.+?)_chunk_(?P<x>\d+)_(?P<y>\d+)\.dxt$",
    re.IGNORECASE,
)
DEFAULT_OUT_DIR = Path("images") / "en"


def rgb565_to_rgb(c: int) -> tuple[int, int, int]:
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return r, g, b


def decode_dxt1_color_block(block: bytes, opaque: bool) -> np.ndarray:
    """Decode an 8-byte DXT color block into 4x4x4 RGBA (alpha 0 or 255)."""
    color0, color1, bits = struct.unpack("<HHI", block)
    c0 = rgb565_to_rgb(color0)
    c1 = rgb565_to_rgb(color1)

    if color0 > color1 or opaque:
        palette = [
            (*c0, 255),
            (*c1, 255),
            tuple((2 * c0[i] + c1[i]) // 3 for i in range(3)) + (255,),
            tuple((c0[i] + 2 * c1[i]) // 3 for i in range(3)) + (255,),
        ]
    else:
        palette = [
            (*c0, 255),
            (*c1, 255),
            tuple((c0[i] + c1[i]) // 2 for i in range(3)) + (255,),
            (0, 0, 0, 0),
        ]

    pixels = np.empty((BLOCK_DIM, BLOCK_DIM, 4), dtype=np.uint8)
    for y in range(BLOCK_DIM):
        for x in range(BLOCK_DIM):
            idx = (bits >> (2 * (y * BLOCK_DIM + x))) & 0x3
            pixels[y, x] = palette[idx]
    return pixels


def decode_dxt1_block(block: bytes) -> np.ndarray:
    """Decode one 8-byte DXT1 block into a 4x4x4 RGBA uint8 array."""
    return decode_dxt1_color_block(block, opaque=False)


def decode_dxt5_alpha_block(block: bytes) -> np.ndarray:
    """Decode the 8-byte DXT5 alpha block into a 4x4 uint8 alpha array."""
    alpha0 = block[0]
    alpha1 = block[1]
    # 48-bit index bitstream in little-endian order across bytes 2..7
    bits = int.from_bytes(block[2:8], "little")

    if alpha0 > alpha1:
        alphas = [
            alpha0,
            alpha1,
            (6 * alpha0 + 1 * alpha1) // 7,
            (5 * alpha0 + 2 * alpha1) // 7,
            (4 * alpha0 + 3 * alpha1) // 7,
            (3 * alpha0 + 4 * alpha1) // 7,
            (2 * alpha0 + 5 * alpha1) // 7,
            (1 * alpha0 + 6 * alpha1) // 7,
        ]
    else:
        alphas = [
            alpha0,
            alpha1,
            (4 * alpha0 + 1 * alpha1) // 5,
            (3 * alpha0 + 2 * alpha1) // 5,
            (2 * alpha0 + 3 * alpha1) // 5,
            (1 * alpha0 + 4 * alpha1) // 5,
            0,
            255,
        ]

    out = np.empty((BLOCK_DIM, BLOCK_DIM), dtype=np.uint8)
    for y in range(BLOCK_DIM):
        for x in range(BLOCK_DIM):
            idx = (bits >> (3 * (y * BLOCK_DIM + x))) & 0x7
            out[y, x] = alphas[idx]
    return out


def decode_dxt5_block(block: bytes) -> np.ndarray:
    """Decode one 16-byte DXT5 block into a 4x4x4 RGBA uint8 array."""
    alpha = decode_dxt5_alpha_block(block[:8])
    # DXT5 color is always opaque; alpha comes from the alpha block.
    pixels = decode_dxt1_color_block(block[8:16], opaque=True)
    pixels[:, :, 3] = alpha
    return pixels


def decode_dxt(path: Path) -> tuple[np.ndarray, int, int]:
    """
    Read a custom DXT1/DXT5 file and return (rgba_array, width, height).
    rgba_array shape is (height, width, 4).
    """
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError(f"{path.name}: file too small ({len(data)} bytes)")

    magic = data[:4]
    if magic == b"DXT1":
        block_size = DXT1_BLOCK_SIZE
        decode_block = decode_dxt1_block
    elif magic == b"DXT5":
        block_size = DXT5_BLOCK_SIZE
        decode_block = decode_dxt5_block
    else:
        raise ValueError(
            f"{path.name}: unsupported magic {magic!r} (expected DXT1 or DXT5)"
        )

    width, height = struct.unpack_from("<II", data, 4)
    payload = data[HEADER_SIZE:]

    blocks_x = (width + BLOCK_DIM - 1) // BLOCK_DIM
    blocks_y = (height + BLOCK_DIM - 1) // BLOCK_DIM
    expected = blocks_x * blocks_y * block_size
    if len(payload) != expected:
        raise ValueError(
            f"{path.name}: expected {expected} data bytes for {width}x{height} "
            f"{magic.decode()}, got {len(payload)}"
        )

    image = np.zeros((blocks_y * BLOCK_DIM, blocks_x * BLOCK_DIM, 4), dtype=np.uint8)
    offset = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = decode_block(payload[offset : offset + block_size])
            y0, x0 = by * BLOCK_DIM, bx * BLOCK_DIM
            image[y0 : y0 + BLOCK_DIM, x0 : x0 + BLOCK_DIM] = block
            offset += block_size

    return image[:height, :width], width, height


def parse_chunk_filename(path: Path) -> tuple[str, int, int] | None:
    """Return (image_name, x, y) for a chunk file, or None if not a chunk."""
    if "chunk" not in path.name.lower():
        return None
    match = CHUNK_RE.match(path.name)
    if not match:
        raise ValueError(
            f"{path.name}: cannot parse (expected <name>_chunk_<x>_<y>.dxt)"
        )
    return match.group("name"), int(match.group("x")), int(match.group("y"))


def merge_chunks(chunks: list[tuple[int, int, Path]]) -> Image.Image:
    """Merge (x, y, path) tiles into one image sized to their bounding box."""
    decoded: list[tuple[int, int, np.ndarray]] = []
    max_x = 0
    max_y = 0

    for x, y, path in sorted(chunks, key=lambda t: (t[1], t[0])):
        rgba, width, height = decode_dxt(path)
        decoded.append((x, y, rgba))
        max_x = max(max_x, x + width)
        max_y = max(max_y, y + height)
        print(f"    {path.name}: {width}x{height} @ ({x}, {y})")

    canvas = np.zeros((max_y, max_x, 4), dtype=np.uint8)
    for x, y, rgba in decoded:
        h, w = rgba.shape[:2]
        canvas[y : y + h, x : x + w] = rgba

    print(f"  Merged size: {max_x}x{max_y}")
    return Image.fromarray(canvas, mode="RGBA")


def group_chunks(directory: Path) -> dict[str, list[tuple[int, int, Path]]]:
    """Group chunk .dxt files in a directory by image name."""
    groups: dict[str, list[tuple[int, int, Path]]] = defaultdict(list)
    for path in sorted(directory.glob("*.dxt")):
        parsed = parse_chunk_filename(path)
        if parsed is None:
            continue
        name, x, y = parsed
        groups[name].append((x, y, path))
    return dict(groups)


def list_standalone_dxts(directory: Path) -> list[Path]:
    """Return .dxt files that are not chunk tiles (decode as whole images)."""
    return [
        path
        for path in sorted(directory.glob("*.dxt"))
        if parse_chunk_filename(path) is None
    ]


def find_dxt_directories(root: Path) -> list[Path]:
    """Return root and/or subdirectories that contain any processable .dxt."""
    candidates = [root, *[p for p in root.rglob("*") if p.is_dir()]]
    found: list[Path] = []
    seen: set[Path] = set()
    for directory in candidates:
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if group_chunks(directory) or list_standalone_dxts(directory):
            found.append(directory)
    return found


def path_relative_to_cwd(path: Path) -> Path:
    """Return path relative to the process cwd when possible."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return Path(*path.parts)


def flat_png_name(dxt_dir: Path, image_name: str) -> str:
    """Build a flat filename from directory path parts + image name.

    Example:
        rooms/images/10_logo + extra_fog_f0
        -> rooms__images__10_logo__extra_fog_f0.png
    """
    parts = [*path_relative_to_cwd(dxt_dir).parts, image_name]
    return "__".join(parts) + ".png"


def process_dxt_directory(directory: Path, out_dir: Path) -> list[Path]:
    """Merge chunk groups and decode standalone .dxt files into out_dir."""
    groups = group_chunks(directory)
    standalone = list_standalone_dxts(directory)
    if not groups and not standalone:
        raise FileNotFoundError(f"No .dxt files found in {directory}")

    print(
        f"Found {len(groups)} chunk image(s) and {len(standalone)} standalone "
        f".dxt file(s) in {directory}"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for name, chunks in sorted(groups.items()):
        print(f"  Image '{name}' ({len(chunks)} chunk(s))")
        image = merge_chunks(chunks)
        out_path = out_dir / flat_png_name(directory, name)
        image.save(out_path)
        written.append(out_path)
        print(f"  Wrote {out_path}")

    for path in standalone:
        print(f"  Standalone '{path.name}'")
        rgba, width, height = decode_dxt(path)
        print(f"    {width}x{height}")
        image = Image.fromarray(rgba, mode="RGBA")
        out_path = out_dir / flat_png_name(directory, path.stem)
        image.save(out_path)
        written.append(out_path)
        print(f"  Wrote {out_path}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert DXT1/DXT5 files to PNG. Chunk tiles are merged; standalone "
            ".dxt files are decoded directly. Scans the given directory and all "
            "subdirectories. Output PNGs are written flat under images/en/."
        )
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory (or parent of directories) containing .dxt files",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR.as_posix()})",
    )
    args = parser.parse_args()

    directory = args.directory
    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    dxt_dirs = find_dxt_directories(directory)
    if not dxt_dirs:
        print(f"Error: No .dxt files found under {directory}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.out
    print(
        f"Scanning {directory}: {len(dxt_dirs)} folder(s) with .dxt files "
        f"-> {out_dir}"
    )
    written: list[Path] = []
    try:
        for dxt_dir in dxt_dirs:
            written.extend(process_dxt_directory(dxt_dir, out_dir))
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Done: {len(written)} PNG(s) from {len(dxt_dirs)} folder(s)")


if __name__ == "__main__":
    main()

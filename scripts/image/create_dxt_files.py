"""
create_dxt_files.py - Encode a PNG/JPG into game DXT1/DXT5 file(s).

Writes the custom 12-byte header used by this project:
    bytes 0-3:  magic "DXT1" or "DXT5"
    bytes 4-7:  width  (uint32 LE)
    bytes 8-11: height (uint32 LE)
followed by raw BC1/BC3 block data.

Usage:
    # Single standalone file (no _chunk_x_y suffix):
    python create_dxt_files.py \\
        --input MI_Dogs_Sleeping_Notice.png \\
        --name objects_a0 \\
        --chunks 0 \\
        --target images/en/rooms/images/36_mansion-e

    # Split into 1024x1024 (or smaller edge) tiles:
    python create_dxt_files.py \\
        --input layer0.png \\
        --name layer0 \\
        --chunks 4 \\
        --target images/en/rooms/images/96_part1

--chunks 0 writes <name>.dxt.
--chunks N (N > 0) splits on a 1024-pixel grid into
    <name>_chunk_<x>_<y>.dxt
and errors if the resulting tile count is not N.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HEADER_SIZE = 12
BLOCK_DIM = 4
TILE_SIZE = 1024
DXT1_BLOCK_SIZE = 8
DXT5_BLOCK_SIZE = 16


def rgb_to_565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def unpack_565(c: int) -> tuple[int, int, int]:
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return r, g, b


def _lerp_rgb(c0: tuple[int, int, int], c1: tuple[int, int, int], a: int, b: int) -> tuple[int, int, int]:
    return tuple((a * c0[i] + b * c1[i]) // (a + b) for i in range(3))


def encode_dxt1_color_block(rgb: np.ndarray, opaque: bool) -> bytes:
    """Encode a 4x4x3 RGB block to 8 DXT color bytes."""
    pixels = rgb.reshape(16, 3).astype(np.int32)
    lum = pixels[:, 0] * 2 + pixels[:, 1] * 5 + pixels[:, 2]
    i0 = int(np.argmin(lum))
    i1 = int(np.argmax(lum))
    c0 = tuple(int(x) for x in pixels[i1])  # brighter -> color0 when possible
    c1 = tuple(int(x) for x in pixels[i0])

    color0 = rgb_to_565(*c0)
    color1 = rgb_to_565(*c1)

    if opaque:
        if color0 < color1:
            color0, color1 = color1, color0
            c0, c1 = c1, c0
        elif color0 == color1:
            # Ensure 4-color mode for opaque DXT5 color blocks.
            if color0 < 0xFFFF:
                color0 += 1
            else:
                color1 -= 1
        p0 = unpack_565(color0)
        p1 = unpack_565(color1)
        palette = [
            p0,
            p1,
            _lerp_rgb(p0, p1, 2, 1),
            _lerp_rgb(p0, p1, 1, 2),
        ]
    else:
        # Punch-through alpha mode when any source pixel is transparent is
        # handled by the caller; here both endpoints may be equal.
        if color0 < color1:
            color0, color1 = color1, color0
            c0, c1 = c1, c0
        # Force color0 <= color1 for 3-color + transparent mode when needed
        # is set by caller via opaque=False and matching color order.
        p0 = unpack_565(color0)
        p1 = unpack_565(color1)
        if color0 > color1:
            palette = [
                p0,
                p1,
                _lerp_rgb(p0, p1, 2, 1),
                _lerp_rgb(p0, p1, 1, 2),
            ]
        else:
            palette = [
                p0,
                p1,
                _lerp_rgb(p0, p1, 1, 1),
                (0, 0, 0),
            ]

    bits = 0
    for i, pix in enumerate(pixels):
        pr, pg, pb = int(pix[0]), int(pix[1]), int(pix[2])
        best = 0
        best_dist = 1 << 30
        for idx, (cr, cg, cb) in enumerate(palette):
            dist = (pr - cr) ** 2 + (pg - cg) ** 2 + (pb - cb) ** 2
            if dist < best_dist:
                best_dist = dist
                best = idx
        bits |= best << (2 * i)

    return struct.pack("<HHI", color0, color1, bits)


def encode_dxt1_block(rgba: np.ndarray) -> bytes:
    """Encode 4x4x4 RGBA to DXT1 (8 bytes). Transparent pixels use index 3."""
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3]
    transparent = alpha < 128

    if not np.any(transparent):
        return encode_dxt1_color_block(rgb, opaque=True)

    opaque_rgb = rgb[~transparent]
    if len(opaque_rgb) == 0:
        return struct.pack("<HHI", 0, 0, 0xFFFFFFFF)

    # Endpoints from opaque pixels; force 3-color mode (color0 <= color1).
    lum = (
        opaque_rgb[:, 0].astype(np.int32) * 2
        + opaque_rgb[:, 1].astype(np.int32) * 5
        + opaque_rgb[:, 2].astype(np.int32)
    )
    c_hi = tuple(int(x) for x in opaque_rgb[int(np.argmax(lum))])
    c_lo = tuple(int(x) for x in opaque_rgb[int(np.argmin(lum))])
    color0 = rgb_to_565(*c_hi)
    color1 = rgb_to_565(*c_lo)
    if color0 > color1:
        color0, color1 = color1, color0

    p0 = unpack_565(color0)
    p1 = unpack_565(color1)
    palette = [p0, p1, _lerp_rgb(p0, p1, 1, 1)]

    bits = 0
    flat = rgba.reshape(16, 4)
    for i, pix in enumerate(flat):
        if pix[3] < 128:
            idx = 3
        else:
            pr, pg, pb = int(pix[0]), int(pix[1]), int(pix[2])
            best = 0
            best_dist = 1 << 30
            for cand, (cr, cg, cb) in enumerate(palette):
                dist = (pr - cr) ** 2 + (pg - cg) ** 2 + (pb - cb) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best = cand
            idx = best
        bits |= idx << (2 * i)
    return struct.pack("<HHI", color0, color1, bits)


def encode_dxt5_alpha_block(alpha: np.ndarray) -> bytes:
    """Encode a 4x4 alpha plane to 8 DXT5 alpha bytes."""
    flat = alpha.reshape(16).astype(np.int32)
    a0 = int(flat.max())
    a1 = int(flat.min())

    if a0 == a1:
        alphas = [a0] + [0] * 7
        # indices all 0
        return bytes([a0, a1]) + bytes(6)

    # Prefer 8-alpha mode (a0 > a1).
    if a0 < a1:
        a0, a1 = a1, a0
    alphas = [
        a0,
        a1,
        (6 * a0 + 1 * a1) // 7,
        (5 * a0 + 2 * a1) // 7,
        (4 * a0 + 3 * a1) // 7,
        (3 * a0 + 4 * a1) // 7,
        (2 * a0 + 5 * a1) // 7,
        (1 * a0 + 6 * a1) // 7,
    ]

    bits = 0
    for i, value in enumerate(flat):
        best = 0
        best_dist = 1 << 30
        for idx, candidate in enumerate(alphas):
            dist = abs(int(value) - candidate)
            if dist < best_dist:
                best_dist = dist
                best = idx
        bits |= best << (3 * i)

    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def encode_dxt5_block(rgba: np.ndarray) -> bytes:
    """Encode 4x4x4 RGBA to DXT5 (16 bytes)."""
    alpha = encode_dxt5_alpha_block(rgba[:, :, 3])
    color = encode_dxt1_color_block(rgba[:, :, :3], opaque=True)
    return alpha + color


def pad_to_blocks(rgba: np.ndarray) -> np.ndarray:
    """Pad height/width up to multiples of 4 by edge-replicating."""
    h, w = rgba.shape[:2]
    pad_h = (BLOCK_DIM - (h % BLOCK_DIM)) % BLOCK_DIM
    pad_w = (BLOCK_DIM - (w % BLOCK_DIM)) % BLOCK_DIM
    if pad_h == 0 and pad_w == 0:
        return rgba
    return np.pad(rgba, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")


def encode_dxt_image(rgba: np.ndarray, fmt: str) -> bytes:
    """Encode an HxWx4 image to raw DXT1 or DXT5 payload (no header)."""
    if fmt not in ("DXT1", "DXT5"):
        raise ValueError(f"unsupported format {fmt}")

    height, width = rgba.shape[:2]
    padded = pad_to_blocks(rgba)
    ph, pw = padded.shape[:2]
    blocks_y = ph // BLOCK_DIM
    blocks_x = pw // BLOCK_DIM
    encode_block = encode_dxt1_block if fmt == "DXT1" else encode_dxt5_block

    out = bytearray()
    for by in range(blocks_y):
        for bx in range(blocks_x):
            y0, x0 = by * BLOCK_DIM, bx * BLOCK_DIM
            block = padded[y0 : y0 + BLOCK_DIM, x0 : x0 + BLOCK_DIM]
            out.extend(encode_block(block))

    expected = blocks_x * blocks_y * (
        DXT1_BLOCK_SIZE if fmt == "DXT1" else DXT5_BLOCK_SIZE
    )
    if len(out) != expected:
        raise RuntimeError(f"encoded size {len(out)} != expected {expected}")

    # width/height in header are the unpadded source dimensions
    return struct.pack("<4sII", fmt.encode("ascii"), width, height) + bytes(out)


def choose_format(rgba: np.ndarray, fmt_arg: str) -> str:
    if fmt_arg != "auto":
        return fmt_arg.upper()
    # Prefer DXT5 when any pixel is not fully opaque (matches most object atlases).
    if np.any(rgba[:, :, 3] < 255):
        return "DXT5"
    return "DXT1"


def iter_tiles(width: int, height: int, tile: int = TILE_SIZE):
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            yield x, y, min(tile, width - x), min(tile, height - y)


def load_rgba(path: Path) -> np.ndarray:
    image = Image.open(path).convert("RGBA")
    return np.asarray(image, dtype=np.uint8)


def write_dxt(path: Path, rgba: np.ndarray, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encode_dxt_image(rgba, fmt)
    path.write_bytes(data)
    print(f"  Wrote {path} ({fmt} {rgba.shape[1]}x{rgba.shape[0]}, {len(data)} bytes)")


def create_dxt_files(
    input_path: Path,
    name: str,
    chunks: int,
    target: Path,
    fmt_arg: str = "auto",
) -> list[Path]:
    if chunks < 0:
        raise ValueError("--chunks must be >= 0")

    rgba = load_rgba(input_path)
    height, width = rgba.shape[:2]
    fmt = choose_format(rgba, fmt_arg)
    print(f"Input {input_path}: {width}x{height}, format={fmt}")

    written: list[Path] = []

    if chunks == 0:
        out_path = target / f"{name}.dxt"
        write_dxt(out_path, rgba, fmt)
        written.append(out_path)
        return written

    tiles = list(iter_tiles(width, height))
    if len(tiles) != chunks:
        raise ValueError(
            f"--chunks {chunks} but image {width}x{height} splits into "
            f"{len(tiles)} tile(s) on a {TILE_SIZE}px grid: "
            + ", ".join(f"({x},{y}) {tw}x{th}" for x, y, tw, th in tiles)
        )

    for x, y, tw, th in tiles:
        tile = rgba[y : y + th, x : x + tw]
        out_path = target / f"{name}_chunk_{x}_{y}.dxt"
        write_dxt(out_path, tile, fmt)
        written.append(out_path)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Encode a PNG/JPG into game DXT1/DXT5 file(s)."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Source PNG or JPG path",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Output image base name (e.g. objects_a0 or layer0)",
    )
    parser.add_argument(
        "--chunks",
        type=int,
        required=True,
        help="0 = single file; N > 0 = split into N tiles on a 1024px grid",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Output directory for the .dxt file(s)",
    )
    parser.add_argument(
        "--format",
        choices=("auto", "dxt1", "dxt5"),
        default="auto",
        help="Compression format (default: auto = DXT5 if any alpha < 255 else DXT1)",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        written = create_dxt_files(
            args.input,
            args.name,
            args.chunks,
            args.target,
            args.format,
        )
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Done: {len(written)} file(s)")


if __name__ == "__main__":
    main()

"""Render the banner's final frame to a crisp PNG for the README.

Pure stdlib: the frame arrays give per-cell glyphs + truecolor, the banner's
own 4x8 glyph masks give per-sample coverage; compose at 2x scale (8x16 px
per terminal cell) and write an RGB PNG with zlib. No pillow, no network.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

import numpy as np

sys.path.insert(0, "src")
from thousand_eyes import banner as _banner  # noqa: E402
from thousand_eyes.banner import Scene  # noqa: E402

SCALE = 2          # 2x: 8x16 px per terminal cell
CELL_W = 4 * SCALE
CELL_H = 8 * SCALE


def _masks() -> tuple[np.ndarray, list[str]]:
    masks, chars = _banner._build_masks()
    return masks, chars


def frame_to_rgb(fr) -> tuple[np.ndarray, int]:
    """(ch, fg, bg, bgd) -> (H_px, W_px, 3) uint8 raster + width in px."""
    ch, fgi, bgi, bgd = fr
    masks, chars = _masks()
    lookup = {c: i for i, c in enumerate(chars)}
    H, W = ch.shape
    img = np.zeros((H * CELL_H, W * CELL_W, 3), np.uint8)
    for r in range(H):
        for c in range(W):
            glyph = str(ch[r, c])
            fg = tuple(int(v) for v in fgi[r, c][:3])
            bg = tuple(int(v) for v in bgi[r, c][:3]) if not bgd[r, c] else (0, 0, 0)
            idx = lookup.get(glyph)
            cell = np.empty((CELL_H, CELL_W, 3), np.uint8)
            if idx is None:
                cell[:, :] = bg          # plain spaces / taglines: bg
                if glyph != " ":
                    # tagline letters are real text; render as solid fg block
                    cell[:, :] = fg
            else:
                cov = masks[idx].reshape(8, 4) > 0.5
                big = np.repeat(np.repeat(cov, SCALE, 0), SCALE, 1)
                cell[:] = np.where(big[..., None], fg, bg)
            y0, x0 = r * CELL_H, c * CELL_W
            img[y0:y0 + CELL_H, x0:x0 + CELL_W] = cell
    return img, W * CELL_W


def write_png(path: str, img: np.ndarray) -> None:
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + row.tobytes() for row in img)   # filter 0 per row
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
           chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/banner.png"
    cols = int(sys.argv[sys.argv.index("--cols") + 1]) if "--cols" in sys.argv else 150
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    scene = Scene(cols)
    img, wpx = frame_to_rgb(scene.final_frame())
    write_png(out, img)
    print(f"wrote {out}: {img.shape[0]}x{wpx} px ({cols} cols of terminal art)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

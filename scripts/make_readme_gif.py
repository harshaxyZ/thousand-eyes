"""Render the live banner animation to an animated GIF for the README.

The README can't run JS, so the animation travels as a GIF instead — the
real frames, downsampled to terminal-cell resolution, palette-quantised to
the banner's own violet ramp. Laptop-safe: pure CPU, numpy + stdlib only,
zero network.

Usage:  python scripts/make_readme_gif.py [out.gif] [--cols N] [--fps F] [--loops L]
"""

from __future__ import annotations

import os
import sys
import time
import struct

import numpy as np

sys.path.insert(0, "src")
from thousand_eyes.banner import Scene, frame_to_text  # noqa: E402

# the banner's own ramp, dark -> violet -> glare (matches LEVELS/shadow colour)
PALETTE = [
    (0, 0, 0), (26, 10, 44), (43, 16, 72), (62, 24, 105),
    (84, 34, 141), (109, 47, 181), (139, 63, 224), (167, 92, 250),
    (196, 130, 255), (216, 166, 255), (232, 196, 255), (245, 224, 255),
    (255, 255, 255), (255, 214, 165), (255, 178, 102), (255, 140, 60),
]


# ---------- helpers: from the banner's own internal arrays ----------
def _cell_indices(fg: np.ndarray, bg: np.ndarray, bgd: np.ndarray) -> np.ndarray:
    """Map each cell to a PALETTE index:  fg colour when visible, bg otherwise."""
    H, W = fg.shape[:2]
    # fg rows: every entry is a 3-tuple (R,G,B); same for bg
    # bgd is bool: True => background variant visible
    idx = np.zeros((H, W), dtype=np.uint8)
    for r in range(H):
        for c in range(W):
            # if bgd[r,c] set => use bg[colour] else fg[colour]
            if bgd[r, c]:
                # find nearest palette entry to bg[r,c]
                bc = tuple(int(x) for x in bg[r, c][:3])
                best, bd = 10**9, 10**9
                for i, (pr, pg, pb) in enumerate(PALETTE):
                    d = (pr - bc[0]) ** 2 + (pg - bc[1]) ** 2 + (pb - bc[2]) ** 2
                    if d < bd:
                        bd = d; best = i
                idx[r, c] = best
            else:
                fc = tuple(int(x) for x in fg[r, c][:3])
                best, bd = 10**9, 10**9
                for i, (pr, pg, pb) in enumerate(PALETTE):
                    d = (pr - fc[0]) ** 2 + (pg - fc[1]) ** 2 + (pb - fc[2]) ** 2
                    if d < bd:
                        bd = d; best = i
                idx[r, c] = best
    return idx


# ---------- GIF writer ----------
def _lzw_encode(data: bytes, min_code: int = 8) -> bytes:
    """GIF LZW. Stdlib only."""
    clear = 1 << min_code
    end = clear + 1
    next_code = end + 1
    width = min_code + 1
    table = {bytes([i]): i for i in range(clear)}
    out = bytearray()
    acc = 0; nbits = 0
    def emit(code: int, w: int) -> None:
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += w
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    emit(clear, width)
    buf = b""
    for b in data:
        cur = buf + bytes([b])
        if cur in table:
            buf = cur; continue
        emit(table[buf], width)
        table[cur] = next_code; next_code += 1
        if next_code == (1 << width) and width < 12:
            width += 1
        if next_code > 4095:
            emit(clear, width)
            table = {bytes([i]): i for i in range(clear)}
            next_code = end + 1
            width = min_code + 1
        buf = bytes([b])
    if buf: emit(table[buf], width)
    emit(end, width)
    if nbits: out.append(acc & 0xFF)
    return bytes(out)


def _sub_blocks(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out.extend(chunk)
    out.append(0)
    return bytes(out)


def write_gif(path: str, idx_frames, delay_cs: int) -> None:
    """idx_frames: list of (H,W) uint8 index images into PALETTE."""
    H, W = idx_frames[0].shape
    with open(path, "wb") as f:
        f.write(b"GIF89a")
        f.write(W.to_bytes(2, "little") + H.to_bytes(2, "little"))  # width, height
        # Packed field: GCT flag=1 | colour resolution=7 | sort=0 | GCT size = 3 => 16 entries
        f.write(bytes([0xF0 | 3, 0, 0]))
        for r, g, b in PALETTE:
            f.write(bytes((r, g, b)))
        # NETSCAPE2.0 loop forever
        f.write(bytes([0x21, 0xFF, 11]) + b"NETSCAPE2.0" + bytes([3, 1, 0, 0]))
        for idx in idx_frames:
            # Graphic Control Extension: packed byte, delay in 1/100 s
            f.write(bytes([0x21, 0xF9, 4, 0x04,
                           delay_cs & 0xFF, (delay_cs >> 8) & 0xFF, 0, 0]))
            # Image Descriptor: separator + origin(0,0) + W/H + packed
            f.write(bytes([0x2C, 0, 0, 0, 0]) + W.to_bytes(2, "little")
                    + H.to_bytes(2, "little") + bytes([0x07]))
            # LZW-coded image data
            flat = idx.tobytes()
            f.write(_sub_blocks(_lzw_encode(flat)))


# ---------- main ----------
def main() -> int:
    argv = sys.argv[1:]
    out = argv[0] if argv else "readme_banner.gif"
    cols = int(argv[argv.index("--cols") + 1]) if "--cols" in argv else 120
    fps = float(argv[argv.index("--fps") + 1]) if "--fps" in argv else 12
    loops = int(argv[argv.index("--loops") + 1]) if "--loops" in argv else 4

    scene = Scene(cols)
    t_end = scene.T_END
    n = int(t_end * fps)
    idx_frames = []
    t0 = time.perf_counter()

    for i in range(n):
        t = i / fps
        fr = scene.frame(t)
        idx = _cell_indices(fr[1], fr[2], fr[3])  # fg/bg/bgd -> palette index
        idx_frames.append(idx)
        if i % 12 == 0:
            print(f"  frame {i+1}/{n}", file=sys.stderr)

    delay_cs = max(2, int(100 / fps))
    write_gif(out, idx_frames, delay_cs)
    dt = time.perf_counter() - t0
    size_kb = os.path.getsize(out) // 1024
    print(f"wrote {out}: {len(idx_frames)} frames, {size_kb} KiB, "
          f"{cols} cols, {fps} fps, render {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
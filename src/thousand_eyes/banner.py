#!/usr/bin/env python3
"""
THOUSAND EYES - final banner (one file, needs numpy: pip install numpy).

High-resolution terminal renderer: every terminal cell is matched against 22 block/quadrant/eighth
symbols from a 4x8 sample grid, so edges land on ~3px steps instead of 12px blocks.
Eye + faceted purple title, letter-by-letter decode (startup only), glare sweeps, two typed taglines.

Run:         python final.py
Play once:   python final.py --once        (~4.3 s, then freezes on the final frame)
Static:      python final.py --static
Options:     --no-eye   --small   --speed 1.5   --fps 30
Import:      from final import show_banner; show_banner(loops=1)

Needs a truecolor terminal (Windows Terminal) and ~150 columns for the full size
(it shrinks automatically on narrower windows).
"""
import argparse
import math
import os
import shutil
import sys
import time

try:
    import numpy as np
except ImportError:  # handled in show_banner
    np = None

TEXT = "THOUSAND EYES"
TAGLINES = ["open-source intelligence", "see everything. miss nothing."]

FONT = {
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "N": ["10001", "11001", "11001", "10101", "10011", "10011", "10001"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
}

SW, SH = 4, 8                       # samples per terminal cell (x, y): every sample ~3px on screen
BGC = (10.0, 8.0, 18.0)             # terminal background the edges blend into
TOP, MID, BOT = (240, 188, 255), (204, 66, 255), (140, 92, 252)
SHADOWC = (112.0, 30.0, 206.0)
GLARE = (255.0, 247.0, 255.0)
# (tile size in samples, letter gap, word gap, eye cols, shadow offset)
LEVELS = [(6.5, 4.0, 24.0, 28, 3), (5.0, 3.0, 18.0, 22, 2), (3.6, 2.0, 12.0, 16, 2)]


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ------------------------------------------------------------------ symbol matching
def _build_masks():
    masks, chars = [], []

    def add(ch, f):
        masks.append(np.array([[1.0 if f(y, x) else 0.0 for x in range(SW)] for y in range(SH)],
                              np.float32).reshape(-1))
        chars.append(ch)
    for n, ch in enumerate("\u2581\u2582\u2583\u2584\u2585\u2586\u2587", 1):
        add(ch, lambda y, x, n=n: y >= SH - n)                     # lower n/8
    add("\u258e", lambda y, x: x < 1)                              # left 1/4
    add("\u258c", lambda y, x: x < 2)                              # left 1/2
    add("\u258a", lambda y, x: x < 3)                              # left 3/4
    quad = {"\u2598": {0}, "\u259d": {1}, "\u2596": {2}, "\u2597": {3}, "\u2580": {0, 1},
            "\u2590": {1, 3}, "\u259a": {0, 3}, "\u259e": {1, 2}, "\u259b": {0, 1, 2},
            "\u259c": {0, 1, 3}, "\u2599": {0, 2, 3}, "\u259f": {1, 2, 3}}
    for ch, s in quad.items():
        add(ch, lambda y, x, s=s: ((2 if y >= SH // 2 else 0) + (1 if x >= SW // 2 else 0)) in s)
    add("\u2588", lambda y, x: True)                               # full block (index K-1)
    return np.array(masks, np.float32), chars


def match(X, MT, N1, N0, tol=500.0):
    """Best 2-colour partition for every cell. X: (N,32,3). Returns index per cell."""
    T = X.sum(1)
    Q = np.einsum("nij,nij->n", X, X)
    S1 = np.matmul(X.transpose(0, 2, 1), MT).transpose(0, 2, 1)      # (N,K,3)
    S0 = T[:, None, :] - S1
    err = Q[:, None] - (S1 * S1).sum(2) / N1 - (S0 * S0).sum(2) / N0
    best = err.argmin(1)
    K = MT.shape[1]
    ebest = err[np.arange(len(best)), best]
    efull = err[:, K - 1]
    best = np.where(efull <= ebest + tol, K - 1, best)
    return best


# ------------------------------------------------------------------ the scene
class Scene:
    T_END = 4.3                      # `--once` stops here
    R0, RD, FL = 0.35, 0.08, 0.22    # letter-by-letter decode (startup only)
    G0, GP, GD = 1.9, 4.2, 1.4       # glare: first start, repeat period, duration
    B0 = 3.6                         # blink: first time (repeats with the glare)
    TAG0, TAGD = (1.2, 1.7), (0.7, 0.8)

    def __init__(self, cols, eye=True, small=False):
        self.MASKS, self.CHARS = _build_masks()
        self.MT = np.ascontiguousarray(self.MASKS.T)
        self.N1 = self.MASKS.sum(1)
        self.N0 = np.maximum(32 - self.N1, 1.0)
        self.K = len(self.CHARS)
        levels = LEVELS[1:] if small else LEVELS
        pick = None
        for lv in levels:
            for e in ([eye, False] if eye else [False]):
                if self._cols(lv, e) <= cols - 1:
                    pick = (lv, e)
                    break
            if pick:
                break
        self.lv, self.eye = pick or (LEVELS[-1], False)
        self._layout()
        self._build_text()
        if self.eye:
            self._build_eye()
        self._static_partition()
        self.gaze = [0.0, 0.0]
        self._gt = None          # scene time of the last gaze update
        self.settle_at = None    # set when the banner should wind down (see show_banner(until=...))
        self.snap = False

    @staticmethod
    def _wt(lv):
        ts, gap, word, _, sh = lv
        return 12 * 5 * ts + 10 * gap + word + sh

    def _cols(self, lv, eye):
        ec = lv[3]
        text_cols = int(math.ceil(self._wt(lv) / SW)) + 1
        return text_cols + ((ec + max(2, ec // 7)) if eye else 0)

    def _layout(self):
        ts, gap, word, ec, sh = self.lv
        self.ts, self.gap, self.word, self.sh = ts, gap, word, sh
        eh = (ec // 4 + 0) if self.eye else 0
        text_h = 7 * ts + sh
        rb = int(math.ceil((text_h + 4) / SH))
        self.Rb = max(rb, 4)
        self.EC = ec
        self.ER = self.Rb
        self.eye_gap = max(2, ec // 7)
        self.ox_cols = (self.EC + self.eye_gap) if self.eye else 0
        tcols = int(math.ceil(self._wt(self.lv) / SW)) + 1
        self.C = self.ox_cols + tcols
        self.HS, self.WS = self.Rb * SH, self.C * SW
        self.rows = self.Rb + len(TAGLINES)

    # ---------------------------------------------------------- title
    def _build_text(self):
        HS, WS, ts = self.HS, self.WS, self.ts
        ox = self.ox_cols * SW
        top = (self.HS - (7 * ts + self.sh)) / 2.0
        self.top = top
        A = np.zeros((HS, WS), np.float32)
        LI = np.full((HS, WS), -1, np.int16)
        COL = np.zeros((HS, WS, 3), np.float32)
        yy = (np.arange(HS) + 0.5)[:, None]
        xx = (np.arange(WS) + 0.5)[None, :]
        self.lx = []
        x0 = float(ox)
        li = 0
        facet = np.zeros((HS, WS), np.float32)
        for ch in TEXT:
            if ch == " ":
                x0 += self.word - self.gap
                continue
            bm = np.pad(np.array([[1.0 if b == "1" else 0.0 for b in r] for r in FONT[ch]], np.float32), 1)
            xa, xb = int(max(0, x0 - 3)), int(min(WS, x0 + 5 * ts + 3))
            ya, yb = int(max(0, top - 3)), int(min(HS, top + 7 * ts + 3))
            X = xx[:, xa:xb]
            Y = yy[ya:yb, :]
            fx = (X - x0) / ts - 0.5
            fy = (Y - top) / ts - 0.5
            ix = np.floor(fx).astype(int)
            iy = np.floor(fy).astype(int)
            tx, ty = fx - ix, fy - iy
            ixc = np.clip(ix + 1, 0, 6)
            iyc = np.clip(iy + 1, 0, 8)
            ixc1 = np.clip(ix + 2, 0, 6)
            iyc1 = np.clip(iy + 2, 0, 8)
            v = (bm[iyc, ixc] * (1 - tx) * (1 - ty) + bm[iyc, ixc1] * tx * (1 - ty) +
                 bm[iyc1, ixc] * (1 - tx) * ty + bm[iyc1, ixc1] * tx * ty)
            a = np.clip((v - 0.46) * ts * 0.95 + 0.5, 0, 1)
            # facet tiles (gem pyramids) in tile-local coords
            tu = (X - x0) / ts
            tv = (Y - top) / ts
            fu, fv = tu - np.floor(tu), tv - np.floor(tv)
            par = (np.floor(tu) + np.floor(tv) + li) % 2
            aa, bb = fv - fu, fv + fu - 1
            pyr = np.where((aa < 0) & (bb < 0), 1.0, np.where((aa > 0) & (bb > 0), -1.0,
                           np.where(aa > 0, 0.35, -0.35)))
            pyr = np.where(par == 0, pyr, -pyr * 0.6)
            sl = (slice(ya, yb), slice(xa, xb))
            upd = a > A[sl]
            A[sl] = np.where(upd, a, A[sl])
            LI[sl] = np.where(upd & (a > 0.3), li, LI[sl])
            facet[sl] = np.where(upd, pyr, facet[sl])
            self.lx.append(x0 + 2.5 * ts)
            x0 += 5 * ts + self.gap
            li += 1
        # vertical gradient + facets + bevel
        t = np.clip((yy - top) / (7 * ts), 0, 1)
        t = np.broadcast_to(t, (HS, WS))
        base = np.where(t[..., None] < 0.42,
                        np.array(TOP) + (np.array(MID) - np.array(TOP)) * (t[..., None] / 0.42),
                        np.array(MID) + (np.array(BOT) - np.array(MID)) * ((t[..., None] - 0.42) / 0.58))
        h = A.copy()
        r = max(2, int(round(ts / 3)))
        for _ in range(2):
            p = np.pad(h, ((r, r), (0, 0)), mode="edge")
            h = sum(p[i:i + HS] for i in range(2 * r + 1)) / (2 * r + 1)
            p = np.pad(h, ((0, 0), (r, r)), mode="edge")
            h = sum(p[:, i:i + WS] for i in range(2 * r + 1)) / (2 * r + 1)
        gy, gx = np.gradient(h)
        bevel = np.clip((gx + gy) * 55.0 * r, -26, 34)
        delta = facet * 13.0 + bevel
        COL = np.clip(base + delta[..., None], 0, 255).astype(np.float32)
        self.A, self.LI, self.COL = A, LI, COL
        sh = self.sh
        As = np.zeros_like(A)
        As[sh:, sh:] = A[:-sh, :-sh]
        self.As = np.clip(As, 0, 1).astype(np.float32)
        LIs = np.full_like(LI, -1)
        LIs[sh:, sh:] = LI[:-sh, :-sh]
        self.LIs = LIs
        self.LIc = np.maximum(LI, 0)
        self.LIsc = np.maximum(LIs, 0)
        ydim, xdim = np.mgrid[0:HS, 0:WS]
        self.D = (xdim + 0.5 + (ydim + 0.5) * 1.5).astype(np.float32)
        self.p0, self.p1 = -120.0, WS + HS * 1.5 + 120.0
        # sparkle glints: positions on solid letter areas
        rng = np.random.RandomState(7)
        pts = np.argwhere(A > 0.99)
        self.sparks = []
        if len(pts):
            for k in range(9):
                y, x = pts[rng.randint(len(pts))]
                self.sparks.append((int(y), int(x), rng.rand() * 6.28))
        kk = 9
        ky, kx = np.mgrid[-kk:kk + 1, -kk:kk + 1].astype(np.float32)
        star = np.maximum(np.exp(-(kx / 5.0) ** 2 - (ky / 0.8) ** 2), np.exp(-(kx / 0.8) ** 2 - (ky / 5.0) ** 2))
        self.star = (star + 0.7 * np.exp(-(kx ** 2 + ky ** 2) / 3.0)).astype(np.float32)

    # ---------------------------------------------------------- eye
    def _build_eye(self):
        hs, ws = self.ER * SH, self.EC * SW
        self.ehs, self.ews = hs, ws
        y = ((np.arange(hs * 2) + 0.5) / 2 - hs / 2.0)[:, None]
        x = ((np.arange(ws * 2) + 0.5) / 2 - ws / 2.0)[None, :]
        self.ex = np.broadcast_to(x, (hs * 2, ws * 2)).astype(np.float32)
        self.ey = np.broadcast_to(y, (hs * 2, ws * 2)).astype(np.float32)
        self.ea = ws / 2.0 - 4.0
        self.ehh = hs / 2.0 - 5.0
        self.eyc = self.HS / 2.0
        self.ecx = ws / 2.0
        a, ri = self.ea, self.ehh * 0.86
        u = np.clip(1 - (self.ex / a) ** 2, 0, 1)
        self.e_u = (u ** 0.8, u ** 0.85, u ** 0.9)
        self.e_ax = np.abs(self.ex)
        self.e_side = 0.6 * (self.e_ax / a) ** 3
        # The iris, pupil distance and catchlights look the same wherever the eye points, so they are
        # rendered once around gaze (0, 0) on a canvas padded by the widest gaze, then sliced per frame.
        maxg = abs(a - ri - 5.0)
        py, px = int(math.ceil(3.0 * 2)) + 2, int(math.ceil(maxg * 2)) + 2
        self.e_pad = (py, px)
        H, W = hs * 2 + 2 * py, ws * 2 + 2 * px
        X = np.broadcast_to(((np.arange(W) - px + 0.5) / 2 - ws / 2.0)[None, :], (H, W)).astype(np.float32)
        Y = np.broadcast_to(((np.arange(H) - py + 0.5) / 2 - hs / 2.0)[:, None], (H, W)).astype(np.float32)
        d = np.sqrt(X * X + Y * Y)
        ang = np.arctan2(Y, X)
        # iris
        rr = (d / ri)[..., None]
        c_in, c_mid, c_out = (np.array(c, np.float32) for c in ((255, 176, 255), (196, 70, 255), (104, 30, 206)))
        k1 = np.clip(rr / 0.62, 0, 1)
        k2 = np.clip((rr - 0.62) / 0.38, 0, 1)
        iris = np.where(rr < 0.62, c_in + (c_mid - c_in) * k1, c_mid + (c_out - c_mid) * k2)
        fib = 1 + 0.13 * np.sin(ang * 26 + rr[..., 0] * 5)[..., None] * np.clip((rr - 0.3) * 4, 0, 1) * np.clip((1 - rr) * 6, 0, 1)
        iris = iris * fib
        iris = np.where(rr > 0.86, iris * (1 - np.clip((rr - 0.86) / 0.14, 0, 1) * 0.6), iris)
        iris = iris + 60 * np.exp(-((rr - 0.42) / 0.05) ** 2)       # bright collarette ring
        ia = np.clip((ri - d) + 0.5, 0, 1)[..., None]
        h1 = np.clip(1 - np.sqrt((X + 0.36 * ri) ** 2 + (Y + 0.42 * ri) ** 2) / (0.22 * ri), 0, 1) ** 0.6
        h2 = np.clip(1 - np.sqrt((X - 0.4 * ri) ** 2 + (Y - 0.44 * ri) ** 2) / (0.1 * ri), 0, 1) ** 0.6
        hl = np.clip(h1 * 0.95 + h2 * 0.7, 0, 1)[..., None]
        self.e_d = d
        self.e_iris = (1 - ia, iris * ia)
        self.e_hl = (1 - hl, 255 * hl)
        self._lid_cache = {}
        self._lid_open = None
        self._lid_open = self._lid(1.0)

    def _lid(self, op):
        """Everything that depends only on how open the eye is. Open (1.0) is kept for good;
        only the latest partly-closed state is cached, since blinks are brief."""
        if op == 1.0 and self._lid_open is not None:
            return self._lid_open
        hit = self._lid_cache.get(op)
        if hit is not None:
            return hit
        y, a, hh = self.ey, self.ea, self.ehh
        u08, u085, u09 = self.e_u
        lh = max(hh * op, 0.9)
        yu = -lh * u08
        yl = lh * 0.8 * u085
        inside = (y >= yu) & (y <= yl) & (self.e_ax <= a)
        dt, db = y - yu, yl - y
        # sclera
        sh = np.clip(0.55 * np.exp(-dt / 5.5) + self.e_side + 0.22 * np.exp(-db / 4.0), 0, 1)[..., None]
        scl = np.array((252, 245, 255), np.float32) * (1 - sh) + np.array((156, 112, 236), np.float32) * sh
        shade = (1 - 0.42 * np.exp(-dt / 3.5))[..., None]
        # lids: neon rim (side wins over top, top over bottom)
        rim_top = dt < 2.6
        rim_side = self.e_ax > a - 2.4
        kt = np.clip(dt / 2.6, 0, 1)[..., None]
        neon_t = np.array((212, 108, 255), np.float32) * (1 - kt) + np.array((120, 36, 214), np.float32) * kt
        rim_col = np.where(rim_side[..., None], np.array((244, 176, 255), np.float32),
                           np.where(rim_top[..., None], neon_t, np.array((158, 72, 244), np.float32)))
        rim = (rim_top | (db < 1.9) | rim_side)[..., None]
        # outside: glow + crease
        dout = np.maximum(np.maximum(yu - y, y - yl), self.e_ax - a)
        glow = np.where(~inside & (dout < 10), 0.42 * np.exp(-np.clip(dout, 0, 20) / 3.2), 0.0)
        bgc = np.array(BGC, np.float32)
        out = bgc + (np.array((176, 74, 255), np.float32) - bgc) * glow[..., None]
        cy = yu - 7.5 * u09
        crease = ((np.abs(y - cy) < 1.0) & (self.e_ax < a * 0.9) & ~inside).astype(np.float32) * 0.65 * min(1.0, op * 1.5)
        out = out + (np.array((150, 64, 232), np.float32) - out) * crease[..., None]
        ins = inside.astype(np.float32).reshape(self.ehs, 2, self.ews, 2).mean((1, 3))
        lid = (inside[..., None], scl, shade, rim, rim_col, out, ins)
        self._lid_cache = {op: lid}
        return lid

    def eye_image(self, op, gx, gy, t):
        """Eye at openness op and gaze (gx, gy); gaze snaps to the 2x supersample grid (1/8 cell)."""
        inside, scl, shade, rim, rim_col, outside, ins = self._lid(round(op, 2))
        py, px = self.e_pad
        sy = max(-py, min(py, int(round(gy * 2))))
        sx = max(-px, min(px, int(round(gx * 2))))
        hs, ws = self.ehs, self.ews
        sl = (slice(py - sy, py - sy + hs * 2), slice(px - sx, px - sx + ws * 2))
        ri = self.ehh * 0.86
        rp = ri * (0.4 + 0.03 * math.sin(2.1 * t))
        one_m_ia, iris_ia = self.e_iris
        one_m_hl, hl255 = self.e_hl
        col = scl * one_m_ia[sl] + iris_ia[sl]
        pa = np.clip((rp - self.e_d[sl]) + 0.5, 0, 1)[..., None]
        col = col * (1 - pa) + np.array((16, 2, 36), np.float32) * pa
        col = (col * one_m_hl[sl] + hl255[sl]) * shade
        col = np.where(rim, rim_col, col)
        out = np.where(inside, col, outside)
        out = out.reshape(hs, 2, ws, 2, 3).mean((1, 3))
        return out.astype(np.float32), ins

    # ---------------------------------------------------------- partition (computed once)
    def _static_partition(self):
        img = np.empty((self.HS, self.WS, 3), np.float32)
        img[:] = BGC
        As, A = self.As[..., None], self.A[..., None]
        img = img * (1 - As) + np.array(SHADOWC, np.float32) * As
        img = img * (1 - A) + self.COL * A
        if self.eye:
            e, _ = self.eye_image(1.0, 0.0, 0.0, 0.0)
            img[:, :self.ews] = e
        X = self._cells(img)
        self.part = match(X, self.MT, self.N1, self.N0)
        self.eye_cells = np.zeros((self.Rb, self.C), bool)
        if self.eye:
            self.eye_cells[:, :self.EC] = True
        self.eye_cells = self.eye_cells.reshape(-1)

    def _cells(self, img):
        return (img.reshape(self.Rb, SH, self.C, SW, 3).transpose(0, 2, 1, 3, 4)
                .reshape(self.Rb * self.C, SH * SW, 3))

    # ---------------------------------------------------------- timeline
    def glare_pos(self, t):
        if t < self.G0:
            return None
        k = int((t - self.G0) // self.GP)
        start = self.G0 + k * self.GP
        lt = t - start
        if lt >= self.GD:
            return None
        if self.settle_at is not None and start > self.settle_at:
            return None              # winding down: let a sweep finish, don't start a new one
        return self.p0 + (self.p1 - self.p0) * smooth(lt / self.GD)

    def glare_map(self, p):
        d = self.D - p
        return np.minimum(1.0, np.exp(-(d / 56.0) ** 2) * 0.7 + np.exp(-(d / 14.0) ** 2) * 0.6).astype(np.float32)

    def _blink(self, t):
        """Lid multiplier for the blink that follows each glare (1.0 = open)."""
        if t < self.B0:
            return 1.0
        m, lt = divmod(t - self.B0, self.GP)
        if lt >= 0.26 or (self.settle_at is not None and self.B0 + m * self.GP > self.settle_at):
            return 1.0
        return abs(lt - 0.13) / 0.13

    def eye_state(self, t, p):
        op = smooth((t - 0.05) / 0.5) * self._blink(t)
        maxg = self.ea - self.ehh * 0.86 - 5.0
        tx = ty = 0.0
        if not self.snap:
            if p is not None:
                gxs = p - self.eyc * 1.5
                tx = max(-1, min(1, (gxs - self.ecx) / (self.WS * 0.35))) * maxg
            elif self.settle_at is not None:
                pass                 # winding down: look straight ahead
            elif t < self.R0 + 12 * self.RD:
                i = max(0, min(11, int((t - self.R0) / self.RD)))
                tx = max(-1, min(1, (self.lx[i] - self.ecx) / (self.WS * 0.4))) * maxg
            elif t > self.G0 + self.GD:
                tx = math.sin(t * 1.1) * maxg * 0.7
                ty = math.sin(t * 0.7 + 1) * 3.0
        if self.snap:
            self.gaze = [tx, ty]
        else:
            # 0.28 per frame at 30 fps, scaled by elapsed time so the eye moves at the same speed at any fps
            k = 0.28 if self._gt is None else 1 - 0.72 ** (max(0.0, t - self._gt) * 30)
            self._gt = t
            self.gaze[0] += (tx - self.gaze[0]) * k
            self.gaze[1] += (ty - self.gaze[1]) * k
        return op, self.gaze[0], self.gaze[1]

    def settled(self, t):
        """True once winding down has begun and no glare, blink or eye movement is still running."""
        if self.settle_at is None or t < self.settle_at or self.glare_pos(t) is not None:
            return False
        if self.eye and (self._blink(t) < 1.0 or abs(self.gaze[0]) > 0.25 or abs(self.gaze[1]) > 0.25):
            return False
        return True

    # ---------------------------------------------------------- one frame -> cell arrays
    def frame(self, t):
        HS, WS = self.HS, self.WS
        p = self.glare_pos(t)
        final = self.snap
        pl = None
        if not final and t < self.R0 + 11 * self.RD + self.FL:
            pl = np.clip((t - (self.R0 + np.arange(12) * self.RD)) / self.FL, 0, 1).astype(np.float32)
        if pl is None:
            vis_t, vis_s = self.A, self.As
            glitch = None
        else:
            rs = np.random.RandomState(int(t * 45) + 11)
            nz = rs.rand(HS // 4 + 1, WS // 4 + 1).repeat(4, 0).repeat(4, 1)[:HS, :WS]
            pt = pl[self.LIc] * (self.LI >= 0)
            ps = np.clip(pl[self.LIsc] * 1.6 - 0.6, 0, 1) * (self.LIs >= 0)
            vis_t = self.A * (nz < pt)
            vis_s = self.As * (nz < ps)
            glitch = np.where(self.LI >= 0, 1 - pt, 0)[..., None]
        if pl is None and getattr(self, "_base", None) is not None:
            img = self._base.copy()
            wgt = self._wgt
        else:
            img = np.empty((HS, WS, 3), np.float32)
            img[:] = BGC
            vs, vt = vis_s[..., None], vis_t[..., None]
            img = img * (1 - vs) + np.array(SHADOWC, np.float32) * vs
            col = self.COL
            if glitch is not None:
                col = col + (np.array((255, 196, 255), np.float32) - col) * (glitch * 0.6)
            img = img * (1 - vt) + col * vt
            wgt = vis_t + 0.35 * vis_s * (1 - vis_t)
            if pl is None:
                self._base, self._wgt = img.copy(), wgt.copy()
        if self.eye:
            op, gx, gy = self.eye_state(t, p)
            key = (round(op, 2), round(gx * 2) / 2, round(gy * 2) / 2, int(t * 4))
            if key != getattr(self, "_ekey", None):
                self._ekey, self._eimg = key, self.eye_image(op, gx, gy, t)
            e, ea = self._eimg
            img[:, :self.ews] = e
            wgt = wgt.copy()
            wgt[:, :self.ews] = ea * 0.8
        if p is not None:
            g = self.glare_map(p) * wgt
            img = img + (np.array(GLARE, np.float32) - img) * g[..., None]
        if not final or True:
            for (y, x, ph) in self.sparks:
                s = max(0.0, math.sin(t * 1.5 + ph)) ** 12
                if s > 0.02 and vis_t[y, x] > 0.9:
                    ya, yb, xa, xb = max(0, y - 9), min(HS, y + 10), max(0, x - 9), min(WS, x + 10)
                    k = self.star[ya - y + 9:yb - y + 9, xa - x + 9:xb - x + 9] * s * 0.85
                    img[ya:yb, xa:xb] += (255 - img[ya:yb, xa:xb]) * k[..., None]
        X = self._cells(img)
        part = self.part
        if self.eye:
            idx = np.nonzero(self.eye_cells)[0]
            part = part.copy()
            part[idx] = match(X[idx], self.MT, self.N1, self.N0)
        Ms = self.MASKS[part]
        S1 = (X * Ms[:, :, None]).sum(1)
        S0 = X.sum(1) - S1
        n1 = self.N1[part][:, None]
        n0 = self.N0[part][:, None]
        fg = S1 / np.maximum(n1, 1)
        bg = S0 / n0
        full = part == self.K - 1
        bg = np.where(full[:, None], fg, bg)
        bgnear = np.abs(bg - np.array(BGC, np.float32)).max(1) < 15
        fgnear = np.abs(fg - np.array(BGC, np.float32)).max(1) < 15
        blank = (full & fgnear) | (~full & fgnear & bgnear)
        bgd = bgnear | full
        R, C = self.Rb, self.C
        chars = np.array(self.CHARS)[part]
        chars = np.where(blank, " ", chars)
        fgi = np.where(blank[:, None], 0, np.clip(fg, 0, 255)).astype(np.uint8)
        bgi = np.where(bgd[:, None], 0, np.clip(bg, 0, 255)).astype(np.uint8)
        bgd = bgd | blank
        ch = chars.reshape(R, C)
        fgi = fgi.reshape(R, C, 3)
        bgi = bgi.reshape(R, C, 3)
        bgd = bgd.reshape(R, C)
        # taglines (real text -> razor sharp)
        tch = np.full((len(TAGLINES), C), " ", dtype="<U1")
        tfg = np.zeros((len(TAGLINES), C, 3), np.uint8)
        for i in range(len(TAGLINES)):
            tag = " ".join(TAGLINES[i].upper())
            if len(tag) > C - 2:
                continue
            start = (C - len(tag)) // 2
            letters = [j for j, c in enumerate(tag) if c != " "]
            prog = 1.0 if final else (t - self.TAG0[i]) / self.TAGD[i]
            nvis = len(letters) if prog >= 1 else int(max(0.0, prog) * len(letters))
            for j in letters[:nvis]:
                c0 = lerp((176, 112, 250), (236, 198, 255), j / max(1, len(tag)))
                if p is not None:
                    d = (start + j) * SW + (self.HS + 8 + i * 16) * 1.5 - p
                    gg = min(1.0, math.exp(-(d / 56.0) ** 2) * 0.7 + math.exp(-(d / 14.0) ** 2) * 0.6) * 0.9
                    c0 = tuple(v + (255 - v) * gg for v in c0)
                tch[i, start + j] = tag[j]
                tfg[i, start + j] = [int(v) for v in c0]
            if 0 < prog < 1 and nvis < len(letters):
                tch[i, start + letters[nvis]] = "\u258c"
                tfg[i, start + letters[nvis]] = (255, 255, 255)
        ch = np.concatenate([ch, tch])
        fgi = np.concatenate([fgi, tfg])
        bgi = np.concatenate([bgi, np.zeros((len(TAGLINES), C, 3), np.uint8)])
        bgd = np.concatenate([bgd, np.ones((len(TAGLINES), C), bool)])
        return ch, fgi, bgi, bgd

    def final_frame(self, t=None):
        self.snap = True
        f = self.frame(self.T_END + 1.0 if t is None else t)
        self.snap = False
        return f


# ------------------------------------------------------------------ terminal output
def _emit(cell, st):
    c, fg, bg, bgd = cell
    want_fg = st["pf"] if c == " " else fg
    want_bg = None if bgd else bg
    codes = []
    if want_fg != st["pf"]:
        codes.append("38;2;%d;%d;%d" % want_fg)
    if want_bg != st["pb"]:
        codes.append("49" if want_bg is None else "48;2;%d;%d;%d" % want_bg)
    st["pf"], st["pb"] = want_fg, want_bg
    return ("\x1b[" + ";".join(codes) + "m" if codes else "") + c


def frame_to_text(fr):
    ch, fg, bg, bgd = fr
    out = []
    for r in range(ch.shape[0]):
        st = {"pf": None, "pb": None}
        cl, fl, bl, dl = ch[r].tolist(), fg[r].tolist(), bg[r].tolist(), bgd[r].tolist()
        s = "".join(_emit((cl[c], tuple(fl[c]), tuple(bl[c]), dl[c]), st) for c in range(len(cl)))
        out.append(s.rstrip(" ") + "\x1b[0m")
    return "\n".join(out)


def diff_frame(prev, fr, st):
    """Redraw only the cells that changed -> tiny writes, no lag, no flicker."""
    ch, fg, bg, bgd = fr
    if prev is None:
        changed = np.ones(ch.shape, bool)
    else:
        pch, pfg, pbg, pbgd = prev
        changed = ((ch != pch) | (bgd != pbgd) | ((fg != pfg).any(2) & (ch != " ")) |
                   ((bg != pbg).any(2) & ~bgd))
    out = []
    for r in np.nonzero(changed.any(1))[0].tolist():
        idx = np.nonzero(changed[r])[0].tolist()
        cl, fl, bl, dl = ch[r].tolist(), fg[r].tolist(), bg[r].tolist(), bgd[r].tolist()
        spans = []
        s = e = idx[0]
        for c in idx[1:]:
            if c - e <= 3:
                e = c
            else:
                spans.append((s, e))
                s = e = c
        spans.append((s, e))
        for s, e in spans:
            if r != st["row"]:
                d = r - st["row"]
                out.append("\x1b[%d%s" % (abs(d), "B" if d > 0 else "A"))
                st["row"] = r
            out.append("\x1b[%dG" % (s + 1))
            for c in range(s, e + 1):
                out.append(_emit((cl[c], tuple(fl[c]), tuple(bl[c]), dl[c]), st))
    return "".join(out)


def _setup():
    os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


def show_banner(animate=True, loops=0, width=None, fps=30, speed=1.0, eye=True, small=False, until=None):
    """loops=0 -> intro once, then glare/blink forever (Ctrl+C to stop).
    loops=1 -> play once (~4.3 s) and freeze on the final frame.
    until=f -> play the intro, keep animating until f() returns True, then ease into the final frame.
    Ctrl+C restores the terminal, then re-raises KeyboardInterrupt."""
    _setup()
    w = sys.stdout
    if np is None:
        w.write("\x1b[1;38;2;200;64;255mTHOUSAND EYES\x1b[0m   (pip install numpy for the full banner)\n")
        return
    cols = width or shutil.get_terminal_size((160, 30)).columns
    sc = Scene(cols, eye=eye, small=small)
    if sc.C > cols - 1:
        w.write("\x1b[1;38;2;200;64;255mTHOUSAND EYES\x1b[0m\n")
        return
    if not animate or not w.isatty():
        w.write(frame_to_text(sc.final_frame()) + "\n")
        w.flush()
        return
    rows = sc.rows
    st = {"row": 0, "pf": None, "pb": None}
    w.write("\x1b[?25l" + "\n" * (rows - 1) + ("\x1b[%dA" % (rows - 1) if rows > 1 else "") + "\r")
    start = time.perf_counter()
    due = start
    prev = None
    el = 0.0
    interrupted = False
    try:
        while True:
            el = (time.perf_counter() - start) * speed
            if until is not None:
                if sc.settle_at is None and el >= sc.T_END and until():
                    sc.settle_at = el
                if sc.settled(el) or (sc.settle_at is not None and el - sc.settle_at > 2.0):
                    break
            elif loops and el >= sc.T_END:
                break
            fr = sc.frame(el)
            w.write("\x1b[?2026h" + diff_frame(prev, fr, st) + "\x1b[?2026l")
            w.flush()
            prev = fr
            due += 1.0 / fps
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                due = time.perf_counter()  # fell behind: don't burst frames to catch up
        w.write(diff_frame(prev, sc.final_frame(el if until is not None else None), st))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if rows - 1 - st["row"] > 0:
            w.write("\x1b[%dB" % (rows - 1 - st["row"]))
        w.write("\x1b[0m\x1b[?25h\n")
        w.flush()
    if interrupted:
        raise KeyboardInterrupt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", action="store_true", help="print once, no animation")
    ap.add_argument("--once", action="store_true", help="play once then exit (~4.3 s)")
    ap.add_argument("--no-eye", action="store_true", help="hide the eye")
    ap.add_argument("--small", action="store_true", help="force a smaller size")
    ap.add_argument("--width", type=int, help="override terminal width")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--speed", type=float, default=1.0, help="1.5 = 1.5x faster")
    a = ap.parse_args()
    try:
        show_banner(animate=not a.static, loops=1 if a.once else 0, width=a.width, fps=a.fps,
                    speed=a.speed, eye=not a.no_eye, small=a.small)
    except KeyboardInterrupt:
        pass

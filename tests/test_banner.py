"""Startup banner: runs without a live terminal by faking a TTY stream."""

from __future__ import annotations

import io
import math
import re
import time

import pytest

np = pytest.importorskip("numpy")  # the banner is an optional feature (pyproject extra "banner")

from thousand_eyes import banner, cli


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_cli_banner_silent_when_not_a_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    cli.print_banner()
    assert capsys.readouterr().out == ""


def test_banner_plays_once_and_ends_on_final_frame(monkeypatch):
    out = FakeTTY()
    monkeypatch.setattr("sys.stdout", out)
    banner.show_banner(loops=1, width=160, speed=20.0)
    text = out.getvalue()
    assert "\x1b[38;2;" in text          # truecolor output
    assert text.endswith("\x1b[0m\x1b[?25h\n")  # cursor restored


def test_banner_keeps_animating_until_told_to_stop(monkeypatch):
    out = FakeTTY()
    monkeypatch.setattr("sys.stdout", out)
    started = time.perf_counter()
    stop_after = 0.6  # real seconds; at speed 10 that is 6 s of scene time, past the 4.3 s intro
    banner.show_banner(until=lambda: time.perf_counter() - started > stop_after, width=160, speed=10.0)
    elapsed = time.perf_counter() - started
    assert elapsed >= stop_after          # did not stop at the end of the intro
    assert elapsed < stop_after + 3.0     # wound down promptly once told to stop
    assert out.getvalue().endswith("\x1b[0m\x1b[?25h\n")


def test_glare_and_blink_keep_repeating_while_running():
    scene = banner.Scene(160)
    period = scene.GP
    for k in range(5):  # well past the 2-3 sweeps where it used to look frozen
        assert scene.glare_pos(scene.G0 + k * period + scene.GD / 2) is not None
        assert scene._blink(scene.B0 + k * period + 0.13) < 0.05


def test_wind_down_lets_running_sweep_finish_but_starts_no_new_one():
    scene = banner.Scene(160)
    mid_sweep = scene.G0 + scene.GP + scene.GD / 2
    scene.settle_at = mid_sweep
    assert scene.glare_pos(mid_sweep + 0.1) is not None              # current sweep finishes
    assert scene.glare_pos(scene.G0 + 2 * scene.GP + 0.5) is None    # next one never starts


def test_eye_image_matches_reference_formula():
    """The cached eye renderer must draw the same pixels as the original per-frame formula."""
    scene = banner.Scene(160)
    assert scene.eye
    ref = _reference_eye(scene, 1.0, 6.0, -2.5, 1.3)
    got, _ = scene.eye_image(1.0, 6.0, -2.5, 1.3)
    assert np.abs(ref - got).max() < 0.01


def _reference_eye(sc, op, gx, gy, t):
    """final.py's eye_image, verbatim apart from local names."""
    x, y, a, hh = sc.ex, sc.ey, sc.ea, sc.ehh
    u = np.clip(1 - (x / a) ** 2, 0, 1)
    lh = max(hh * op, 0.9)
    yu = -lh * u ** 0.8
    yl = lh * 0.8 * u ** 0.85
    inside = (y >= yu) & (y <= yl) & (np.abs(x) <= a)
    dt, db = y - yu, yl - y
    ri = hh * 0.86
    rp = ri * (0.4 + 0.03 * math.sin(2.1 * t))
    dx, dy = x - gx, y - gy
    d = np.sqrt(dx * dx + dy * dy)
    ang = np.arctan2(dy, dx)
    sh = np.clip(0.55 * np.exp(-dt / 5.5) + 0.6 * (np.abs(x) / a) ** 3 + 0.22 * np.exp(-db / 4.0), 0, 1)[..., None]
    scl = np.array((252, 245, 255), np.float32) * (1 - sh) + np.array((156, 112, 236), np.float32) * sh
    rr = (d / ri)[..., None]
    c_in, c_mid, c_out = (np.array(c, np.float32) for c in ((255, 176, 255), (196, 70, 255), (104, 30, 206)))
    k1 = np.clip(rr / 0.62, 0, 1)
    k2 = np.clip((rr - 0.62) / 0.38, 0, 1)
    iris = np.where(rr < 0.62, c_in + (c_mid - c_in) * k1, c_mid + (c_out - c_mid) * k2)
    fib = 1 + 0.13 * np.sin(ang * 26 + rr[..., 0] * 5)[..., None] * np.clip((rr - 0.3) * 4, 0, 1) * np.clip((1 - rr) * 6, 0, 1)
    iris = iris * fib
    iris = np.where(rr > 0.86, iris * (1 - np.clip((rr - 0.86) / 0.14, 0, 1) * 0.6), iris)
    iris = iris + 60 * np.exp(-((rr - 0.42) / 0.05) ** 2)
    ia = np.clip((ri - d) + 0.5, 0, 1)[..., None]
    col = scl * (1 - ia) + iris * ia
    pa = np.clip((rp - d) + 0.5, 0, 1)[..., None]
    col = col * (1 - pa) + np.array((16, 2, 36), np.float32) * pa
    h1 = np.clip(1 - np.sqrt((x - (gx - 0.36 * ri)) ** 2 + (y - (gy - 0.42 * ri)) ** 2) / (0.22 * ri), 0, 1) ** 0.6
    h2 = np.clip(1 - np.sqrt((x - (gx + 0.4 * ri)) ** 2 + (y - (gy + 0.44 * ri)) ** 2) / (0.1 * ri), 0, 1) ** 0.6
    hl = np.clip(h1 * 0.95 + h2 * 0.7, 0, 1)[..., None]
    col = col * (1 - hl) + 255 * hl
    col = col * (1 - 0.42 * np.exp(-dt / 3.5))[..., None]
    rim_top = dt < 2.6
    rim_bot = db < 1.9
    rim_side = np.abs(x) > a - 2.4
    neon_t = np.array((212, 108, 255), np.float32) * (1 - np.clip(dt / 2.6, 0, 1))[..., None] + np.array((120, 36, 214), np.float32) * np.clip(dt / 2.6, 0, 1)[..., None]
    col = np.where((rim_top & inside)[..., None], neon_t, col)
    col = np.where((rim_bot & inside & ~rim_top)[..., None], np.array((158, 72, 244), np.float32), col)
    col = np.where((rim_side & inside)[..., None], np.array((244, 176, 255), np.float32), col)
    dout = np.maximum(np.maximum(yu - y, y - yl), np.abs(x) - a)
    glow = np.where(~inside & (dout < 10), 0.42 * np.exp(-np.clip(dout, 0, 20) / 3.2), 0.0)
    bgc = np.array(banner.BGC, np.float32)
    out = bgc + (np.array((176, 74, 255), np.float32) - bgc) * glow[..., None]
    cy = yu - 7.5 * u ** 0.9
    crease = ((np.abs(y - cy) < 1.0) & (np.abs(x) < a * 0.9) & ~inside).astype(np.float32) * 0.65 * min(1.0, op * 1.5)
    out = out + (np.array((150, 64, 232), np.float32) - out) * crease[..., None]
    out = np.where(inside[..., None], col, out)
    return out.reshape(sc.ehs, 2, sc.ews, 2, 3).mean((1, 3)).astype(np.float32)


def test_final_frame_shows_both_taglines():
    final = banner.frame_to_text(banner.Scene(160).final_frame())
    visible = re.sub(r"\x1b\[[0-9;]*m", "", final)
    assert "O P E N - S O U R C E   I N T E L L I G E N C E" in visible
    assert "S E E   E V E R Y T H I N G .   M I S S   N O T H I N G ." in visible


def test_narrow_terminal_falls_back_cleanly(monkeypatch):
    """A too-narrow console must not crash: show_banner prints the plain
    one-line title when no Scene level fits, and a Scene never renders
    zero rows."""
    scene = banner.Scene(30)
    assert scene.rows >= 1
    art = banner.frame_to_text(scene.final_frame())
    assert len(art.splitlines()) >= 1
    stream = io.StringIO()
    stream.isatty = lambda: True
    monkeypatch.setattr(banner.sys, "stdout", stream)
    banner.show_banner(animate=False, loops=1, width=30)
    out = stream.getvalue()
    assert "THOUSAND EYES" in out
    assert "\x1b[38;2" not in out or out.count("\x1b") <= 1  # plain fallback


def test_static_banner_fits_terminal_width():
    for width in (80, 100, 120, 160):
        scene = banner.Scene(width)
        assert scene.C <= width - 1
        lines = banner.frame_to_text(scene.final_frame()).splitlines()
        for line in lines:
            assert len(re.sub(r"\x1b\[[0-9;]*m", "", line)) <= width - 1

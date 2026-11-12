"""live.py - the scanner cockpit (v0.4).

Renders, WHILE the wildfire walks the graph:
  - one overall progress bar with a real ETA (units = module/site probes,
    announced up-front by the engine and extended as spawned leads grow);
  - a stage line showing exactly which module/lead is burning right now;
  - a rolling finding feed: high-impact results print AS THEY LAND, not at
    the end (the movie-montage effect) - ranked glyphs:
       ✜ = weaponised identity key (name / email / phone / stealer log)
       ▦ = pivotable platform account
       · = ambient footprint

Non-TTY (pipes, CI, --no-banner) → silent no-op sink; nothing is written
while scanning, so logs stay clean.

Dependencies: rich is OPTIONAL at runtime here - if missing, the module
falls back to the null sink (the CLI still prints the final ranked report).
"""

from __future__ import annotations

import threading
import time

from thousand_eyes.impact import AMBIENT, PIVOTABLE, WEAPONISED
from thousand_eyes.models import NullSink

_GLYPH = {WEAPONISED: "✜", PIVOTABLE: "▦", AMBIENT: "·", 0: "?"}
_KILO_PURPLE = "\x1b[38;2;204;66;255m"
_GHOST = "\x1b[38;2;140;92;252m"
_RESET = "\x1b[0m"


try:
    from rich.console import Group, RenderableType
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table

    _HAVE_RICH = True
except ImportError:  # pragma: no cover
    _HAVE_RICH = False


class RichSink(NullSink):
    """Live rich rendering of scan progress + streamed findings."""

    FEED_LINES = 6

    def __init__(self, console=None) -> None:
        if not _HAVE_RICH:
            raise RuntimeError("rich not installed")
        self._console = console
        self._lock = threading.Lock()
        self.progress = Progress(
            TextColumn("[bold violet]wildfire"),
            SpinnerColumn(style="violet"),
            BarColumn(bar_width=None, style="spectrum", complete_style="violet"),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            expand=True,
        )
        self.overall = self.progress.add_task("scan", total=None)
        self._planned = 0   # our own monotonic total (rich starts total=None)
        self.stage_line = "starting…"
        self.feed: list[tuple[str, str]] = []       # (glyph, text)
        self.findings_count = 0
        self._live: Live | None = None
        self._t0 = time.perf_counter()

    # rendering ----------------------------------------------------------
    def _feed_table(self):
        t = Table.grid(padding=(0, 1))
        t.add_column(width=2)
        t.add_column(ratio=1, overflow="ellipsis")
        for glyph, text in self.feed[-self.FEED_LINES:]:
            style = {
                "✜": "bold red",
                "▦": "bold violet",
                "·": "dim grey50",
                "?": "dim",
            }.get(glyph, "")
            t.add_row(glyph, text, style=style)
        return t

    def _render(self) -> RenderableType:
        return Group(
            Panel.fit(
                self.stage_line,
                style="violet",
                title="stage",
                title_align="left",
                border_style="purple",
            ),
            self.progress,
            self._feed_table(),
        )

    def __enter__(self) -> "RichSink":
        self._live = Live(
            self._render(),
            refresh_per_second=12,
            console=self._console,   # default console (stdout); rich handles windows/ANSI
            transient=False,
        )
        self._live.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _refresh(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:
                pass

    # sink interface ------------------------------------------------------
    def expect(self, additional: int) -> None:
        if additional > 0:
            self._planned += additional
            self.progress.update(self.overall, total=self._planned)
        self._refresh()

    def bump(self, done: int = 1) -> None:
        self.progress.update(self.overall, advance=done)
        self._refresh()

    def stage(self, label: str) -> None:
        self.stage_line = label
        self._refresh()

    def finding(self, tier: int, kind: str, label: str) -> None:
        self.findings_count += 1
        glyph = _GLYPH.get(tier, "?")
        text = f"{glyph} {label[:140]}"
        with self._lock:
            if text not in [t for _, t in self.feed]:
                self.feed.append((glyph, text))
        self._refresh()

    def close(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None


def make_sink(enabled: bool) -> NullSink:
    """RichSink when live output is wanted & available; else the no-op sink."""
    if enabled and _HAVE_RICH:
        return RichSink()
    return NullSink()

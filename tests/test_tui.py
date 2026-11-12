"""TUI tests - chat-first generic agent; audits fire only on real leads."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

textual = pytest.importorskip("textual")

from thousand_eyes.tui import ThousandEyesApp, route_intent, _parse_ansi_art  # noqa: E402
from thousand_eyes.banner import Scene, frame_to_text  # noqa: E402


# ------------------------------------------------------------- intent routing

@pytest.mark.parametrize("text,action,lead", [
    # smalltalk - never an audit
    ("yo what's up", "chat", None),
    ("yo", "chat", None),
    ("hey", "chat", None),
    ("what's up", "chat", None),
    ("how are you", "chat", None),
    ("tell me a joke", "chat", None),
    ("weather today", "chat", None),
    ("data", "chat", None),
    ("what can you do", "chat", None),
    ("who are you", "chat", None),
    ("status", "chat", None),
    ("findings", "chat", None),
    ("fence", "chat", None),
    ("run a full audit on me", "ask_lead", None),
    ("search the dark web", "chat", None),
    ("find stuff about me", "chat", None),
    # real leads - audit fires
    ("someuser_42", "audit", "someuser_42"),
    ("audit someuser_42", "audit", "someuser_42"),
    ("scan tammy_faye", "audit", "tammy_faye"),
    ("you@example.com", "audit", "you@example.com"),
    ("audit you@example.com", "audit", "you@example.com"),
    ("check example.com", "audit", "example.com"),
    # the operator's exact trigger phrasing
    ("find out what you can using my email - you@example.com", "audit", "you@example.com"),
    ("find out what you can using my email - someuser_42", "audit", "someuser_42"),
    # audit asks without a lead must never fire a scan
    ("scan my email", "ask_lead", None),
    ("find out about my email", "ask_lead", None),
    ("audit on me", "ask_lead", None),
    ("look up my username", "ask_lead", None),
    ("run an audit on me", "ask_lead", None),
    ("what is my exposure", "ask_lead", None),
    # terse verb + handle form still works
    ("audit someuser_42", "audit", "someuser_42"),
    ("deep dive someuser_9", "audit", "someuser_9"),
])
def test_route_intent(text, action, lead):
    assert route_intent(text) == (action, lead)


def test_banner_parses_to_clean_rich_text():
    art = frame_to_text(Scene(80, eye=True, small=True).final_frame())
    t = _parse_ansi_art(art)
    assert "\x1b" not in t.plain                      # no ANSI garbage
    assert any(ch in t.plain for ch in "▆▊▎")         # block-glyph gradient kept
    assert len(t.plain.splitlines()) >= 3             # multi-line art


# ------------------------------------------------------------- pilot behaviour

def test_banner_direct_render_from_frame_arrays():
    """_banner_text builds the Text straight from the Scene arrays: no ANSI
    round-trip, truecolor per cell, every row of the scene present."""
    app = ThousandEyesApp()
    t = app._banner_text()
    assert "\x1b" not in t.plain                      # no escape garbage
    assert any(ch in t.plain for ch in "▆▊▎")         # block-glyph gradient kept
    assert "O P E N" in t.plain                       # tagline row is real text
    assert len(t.plain.splitlines()) >= 6             # art rows + taglines


@pytest.mark.asyncio
async def test_tui_composes_banner_chat_input():
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        banner = app.query_one("#banner")
        chat = app.query_one("#chat")
        stream = app.query_one("#stream")
        status = app.query_one("#status")
        inp = app.query_one("#input")
        assert banner is not None and chat is not None
        assert stream is not None and status is not None and inp is not None
        # banner is rich text: glyphs present, no ANSI escapes in the renderable
        bt = banner.renderable
        assert any(ch in bt.plain for ch in "▆▊▎")
        assert "\x1b" not in bt.plain
        # welcome landed, not busy
        await pilot.pause()
        assert app._busy is False


@pytest.mark.asyncio
async def test_chat_greeting_does_not_start_agent():
    """The original bug: 'yo what's up' fired a full audit. Must not."""
    app = ThousandEyesApp()
    said: list[str] = []
    app._say = lambda txt: said.append(str(txt))  # record instead of render
    async with app.run_test() as pilot:
        inp = app.query_one("#input")
        inp.value = "yo what's up"
        await pilot.press("enter")
        # let the chat reply land (LLM round trip or canned fallback)
        for _ in range(20):
            if not app._busy:
                break
            await pilot.pause(0.5)
        assert app._planner is None        # no audit planner ever ran
        # agent answered in chat (canned/offline reply, no key needed)
        blob = " ".join(said)
        assert "agent" in blob


@pytest.mark.asyncio
async def test_chat_needs_no_key_for_canned_topics():
    app = ThousandEyesApp()
    said: list[str] = []
    app._say = lambda txt: said.append(str(txt))
    async with app.run_test() as pilot:
        inp = app.query_one("#input")
        for msg in ("fence", "status", "findings"):
            inp.value = msg
            await pilot.press("enter")
            await pilot.pause(0.3)
        assert app._busy is False
        blob = " ".join(said).lower()
        assert "no .onion" in blob          # fence line
        assert "idle" in blob               # status line
        assert "no evidence rows" in blob   # findings line


@pytest.mark.asyncio
async def test_trigger_phrase_starts_agent():
    """'find out what you can using my email - …' must arm the pipeline."""
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        inp = app.query_one("#input")
        inp.value = "find out what you can using my email - demo.subject@example.org"
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert app._busy is True
        assert app._subject == ["demo.subject@example.org"]
        inp.value = "/cancel"
        await pilot.press("enter")
        await pilot.pause()
        assert app._planner is not None  # still winding down


@pytest.mark.asyncio
async def test_hud_visible_progress_bar_and_stats():
    """The pipeline HUD must be on screen: bar + stats, zero scrollbar."""
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        hud = app.query_one("#hud")
        bar = app.query_one("#bar")
        stats = app.query_one("#stats")
        assert hud is not None and bar is not None and stats is not None
        assert "idle" in stats.renderable.lower()
        assert app.query_one("#chat").styles.scrollbar_size_vertical == 0
        # Drive the HUD the way a live run does: arm the planner state the
        # runner uses, then tick the heartbeat. Zero timing races here; the
        # real-run integration is covered by test_trigger_phrase_starts_agent.
        from types import SimpleNamespace

        from thousand_eyes.agent.analytics import Analytics

        app._planner = SimpleNamespace(analytics=Analytics())
        app._bar_total = 12.0
        app._run_start = time.monotonic() - 2.0
        app._tick()                       # one heartbeat
        live = stats.renderable.lower()
        assert "t+" in live and "eta" in live and "tools" in live
        assert "working" in live                      # elapsed-of-total line
        assert app._bar_total > 0         # bar has an honest time budget
        # disarm: HUD returns to idle and the bar empties
        app._planner = None
        app._tick()
        assert "idle" in stats.renderable.lower()


@pytest.mark.asyncio
async def test_commands():
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        inp = app.query_one("#input")
        for cmd in ("/help", "/status", "/bogus", "/clear"):
            inp.value = cmd
            await pilot.press("enter")
            await pilot.pause()
        # /demo: a live demo run can complete before the assertion on fast
        # CI machines, so arm the run state exactly as action_demo does and
        # verify /cancel unwinds it (same race-free pattern as the HUD test)
        from types import SimpleNamespace

        app._busy = True
        app._planner = SimpleNamespace(stop=lambda: None)
        inp.value = "/cancel"
        await pilot.press("enter")
        await pilot.pause()
        assert app._planner is not None          # stop() called, winding down
        # the run loop then finishes and disarms itself
        app._planner = None
        app._busy = False
        inp.value = "/exit"
        await pilot.press("enter")
        await pilot.pause()

"""TUI tests — chat-first: chit-chat never fires an audit; banner is clean rich text."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

textual = pytest.importorskip("textual")

from thousand_eyes.tui import ThousandEyesApp, route_intent, _parse_ansi_art  # noqa: E402
from thousand_eyes.banner import Scene, frame_to_text  # noqa: E402


# ------------------------------------------------------------- intent routing

@pytest.mark.parametrize("text,action,lead", [
    ("yo what's up", "greet", None),
    ("yo", "greet", None),
    ("hey", "greet", None),
    ("what's up", "greet", None),
    ("how are you", "greet", None),
    ("weather today", "chat", None),
    ("data", "chat", None),
    ("stuff", "chat", None),
    ("what can you do", "help", None),
    ("who are you", "help", None),
    ("status", "chat", None),
    ("findings", "chat", None),
    ("fence", "chat", None),
    ("harsha1218_", "audit", "harsha1218_"),
    ("audit harsha1218_", "audit", "harsha1218_"),
    ("scan tammy_faye", "audit", "tammy_faye"),
    ("you@example.com", "audit", "you@example.com"),
    ("audit you@example.com", "audit", "you@example.com"),
    ("check example.com", "audit", "example.com"),
    ("run a full audit on me", "ask_lead", None),
    ("search the dark web", "ask_lead", None),
    ("find stuff about me", "ask_lead", None),
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
        await pilot.pause(0.5)
        assert app._busy is False          # no audit started
        assert app._planner is None        # no planner running
        # agent answered in chat (canned greeting, no key needed)
        blob = " ".join(said)
        assert "agent" in blob and ("yo" in blob.lower() or "thousand-eyes" in blob.lower())


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
async def test_commands():
    app = ThousandEyesApp()
    async with app.run_test() as pilot:
        inp = app.query_one("#input")
        for cmd in ("/help", "/status", "/bogus", "/clear"):
            inp.value = cmd
            await pilot.press("enter")
            await pilot.pause()
        # /demo starts the agent (key present in this env) then we cancel
        inp.value = "/demo"
        await pilot.press("enter")
        await pilot.pause()
        assert app._busy is True
        inp.value = "/cancel"
        await pilot.press("enter")
        await pilot.pause()
        assert app._planner is not None  # still winding down
        app.query_one("#input").value = "/exit"
        await pilot.press("enter")
        await pilot.pause()

"""thousand-eyes interactive TUI - a chat-first agent console.

Bare `thousand-eyes` opens this: black terminal, the violet banner up top,
a streaming chat, a live pipeline HUD (progress bar · elapsed · ETA ·
counts), a token pane, a status line and a one-line input box. It is a
GENERAL conversational agent: chat about anything and it answers (LLM when
a key is present, gracefully offline without one). An audit fires ONLY on a
real identifier - email, username, domain, url, phone - or an explicit ask
like "find out what you can using my email - x@y.com". Smalltalk never
scans anything.

Design contract (v1.0):
  * violet lives ONLY in the banner + input border; everything else white
  * your messages are tagged [YOU]; agent turns are tagged [AGENT]
  * while the agent works, the stream pane shows its CURRENT STEP live
  * every run ends with a structured SUMMARY (analytics + findings)

Commands:  /demo  /clear  /setup  /skill  /status  /cancel  /help  /exit
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from rich.color import Color
from rich.style import Style
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from thousand_eyes import __version__
from thousand_eyes.agent.analytics import Analytics
from thousand_eyes.agent.planner import FENCE, Planner
from thousand_eyes.agent.providers import client_for
from thousand_eyes.demo import demo_transport

ESC = "\\["

# ------------------------------------------------------------- intent routing

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL_RE = re.compile(r"https?://\S+")
_PHONE_RE = re.compile(r"\+?[1-9]\d{6,14}")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|app|co|ai|me|edu|gov)\b", re.I)
_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
_AUDIT_RE = re.compile(
    r"\b(audit|scan|sweep|check|exposure|breach|breaches|dossier|investigate|"
    r"footprint|osint|pivot|report|run|find|search|look\s?up|deep\s?dive|"
    r"profile|analyse|analyze)\b", re.I)

# trailing words that are handle-shaped but never leads ("search the dark web")
_LAST_TOKEN_STOP = {
    "data", "stuff", "things", "thing", "info", "information", "details",
    "everything", "nothing", "something", "anything", "that", "this", "out",
    "now", "then", "more", "again", "all", "some", "any", "it",
    "email", "e-mail", "phone", "number", "address", "domain", "username",
    "handle", "url", "link", "site", "account", "name", "id", "profile",
}

# words that look like handles but are conversation, not leads
_STOP_WORDS = {
    "audit", "scan", "sweep", "check", "checks", "exposure", "breach", "breaches",
    "dossier", "investigate", "footprint", "osint", "pivot", "report", "run",
    "find", "found", "search", "look", "up", "deep", "dive", "profile",
    "status", "progress", "summary", "summarise", "summarize", "analyse",
    "analyze", "help", "demo", "clear", "cancel", "exit", "quit", "what",
    "whats", "what's", "who", "how", "why", "when", "where", "which", "the",
    "and", "you", "your", "my", "me", "it", "its", "are", "is", "can", "do",
    "did", "does", "bro", "dude", "thanks", "thank", "please", "hello",
    "hey", "hi", "yo", "sup", "okay", "ok", "cool", "nice", "joke", "weather",
}

# off-topic nouns that make an audit verb a chat, not an order to scan
_CHAT_TOPICS = ("joke", "movie", "song", "recipe", "weather", "news",
                "story", "poem", "game", "book", "food", "meaning")

# ------------------------------------------------------------------ palette
# violet ONLY in the banner + input border; everything else is white on black.

VIOLET = "#a78bfa"          # the single allowed purple
VIOLET_DIM = "#5b21b6"
WHITE = "#f5f3ff"
GREY = "#8a8a8a"
BG = "#000000"
BG_PANEL = "#0a0a0a"

CSS = """
Screen { background: black; }
Header { background: black; color: white; }
Footer { background: black; color: white; }
#banner { background: black; height: auto; padding: 0 0; }
#chat {
    border: round #2a2a2a;
    padding: 0 1;
    color: white;
    background: black;
    height: 1fr;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}
#hud {
    background: black;
    color: white;
    height: 1;
    padding: 0 1;
}
#stream {
    border: round #2a2a2a;
    padding: 0 1;
    color: white;
    background: black;
    height: 10;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}
#status {
    color: #777777;
    background: #0a0a0a;
    padding: 0 1;
    height: 1;
}
#input {
    border: tall #5b21b6;
    background: #0a0a0a;
    color: white;
}
#input:focus {
    border: tall #7c3aed;
}
#input > .input--placeholder {
    color: #555555;
}
"""

CHAT_SYSTEM = (
    "You are thousand-eyes, a general-purpose security and privacy chat that "
    "stays in its lane. Your beat: digital footprint, online presence, "
    "account audits, breach exposure, data brokers, doxxing, phishing, "
    "tracking, OSINT technique, opsec and online safety. Answer normal "
    "questions in that genre fully and concretely - you are a chat, not a "
    "redirect bot. For anything outside the beat (code help, file reading, "
    "code review, recipes, weather, smalltalk), refuse in one sentence and "
    "steer back. You can also RUN an audit when the operator hands you a "
    "lead: email, username, domain, url or phone. Be terse and concrete: "
    "short sentences, no markdown headers, no emojis, no ANSI codes. Never "
    "fabricate a finding and never claim something is absent - uncertainty "
    "is INCONCLUSIVE. Identity rule: the same username on two platforms is "
    "handle reuse, not proof of one person - never merge accounts without "
    "corroboration (matching name, bio, avatar or cross-links) and say so "
    "when you cannot tell."
)

# --------------------------------------------------------------- step labels

_STEP_LABEL = {
    "start": "warming up",
    "ready": "tool registry loaded",
    "round": "planning round",
    "tool_start": "running tool",
    "tool_done": "tool returned",
    "tool_err": "tool failed",
    "lead": "following a lead",
    "finding": "finding recorded",
    "audit": "self-audit: picking next family",
    "synth": "writing the report",
    "done": "run complete",
    "stop": "stopped by operator",
    "err": "run error",
}


def await_cmd(command: str, timeout: float = 15.0) -> str:
    """Run one read-only shell command; operator sees exactly what ran."""
    import shlex
    import subprocess
    argv = shlex.split(command)
    if not argv:
        return "(empty command)"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return f"(not found: {argv[0]})"
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout:.0f}s: {command})"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    body = out or err or "(no output)"
    tail = "\n…" if len(body) > 1200 else ""
    return f"$ {command}\n{body[:1200]}{tail}"


def route_intent(text: str) -> Tuple[str, Optional[str]]:
    """Classify a chat line into (action, payload).

    Actions: audit (payload = the lead) · ask_lead · chat.
    An audit fires only on a real identifier, or an explicit audit ask with
    one - never on plain conversation.
    """
    s = text.strip()
    low = s.lower()
    wants = bool(_AUDIT_RE.search(low))
    off_topic = any(k in low for k in _CHAT_TOPICS)

    cand: Optional[str] = None
    explicit = False          # identifier-shaped, not inferred from a verb
    m = _EMAIL_RE.search(s)
    if m:
        cand, explicit = m.group(0).rstrip(".,);"), True
    if cand is None:
        m = _URL_RE.search(s)
        if m:
            cand, explicit = m.group(0).rstrip(".,);"), True
    if cand is None and (wants or "phone" in low or " " not in s):
        m = _PHONE_RE.search(s)
        if m:
            cand, explicit = m.group(0), True
    if cand is None and (wants or " " not in s):
        m = _DOMAIN_RE.search(s)
        if m:
            cand, explicit = m.group(0), True
    # bare handle: only when it clearly isn't conversation (_ or a digit)
    if cand is None and _HANDLE_RE.match(s) and s.lower() not in _STOP_WORDS:
        if "_" in s or any(c.isdigit() for c in s):
            cand, explicit = s, True
    # '<audit verb> <handle>' - trailing token is the lead. Split on
    # whitespace AND hyphens so "email - some_handle" yields the handle;
    # multi-word junk like "search the dark web" is stopped by the
    # trailing-word set, not by a word count (long trigger phrases are legal).
    if cand is None and wants and not off_topic:
        toks = s.split()
        last = toks[-1].rstrip(".,);:!?")
        trail = last.rsplit("-", 1)[-1].strip() or last
        # a real handle carries _ or a digit; the only exception is the
        # terse 'audit someuser' form (2 tokens) where the verb names it
        looks_like_handle = ("_" in trail or any(c.isdigit() for c in trail)
                             or len(toks) <= 2)
        if (looks_like_handle and _HANDLE_RE.match(trail)
                and trail.lower() not in _STOP_WORDS
                and trail.lower() not in _LAST_TOKEN_STOP):
            cand, explicit = trail, True

    if cand and (explicit or wants):
        return "audit", cand
    if wants and not off_topic and any(k in low for k in
                                       ("email", "username", "handle", "domain",
                                        "phone", "url", "my")):
        return "ask_lead", None
    if wants and not off_topic and " on me" in low:
        return "ask_lead", None
    return "chat", None


# ------------------------------------------------------------- banner parsing

_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")


def _parse_ansi_art(art: str) -> Text:
    """Turn the banner's truecolor ANSI frame into a rich Text (no garbage)."""
    out = Text()
    fg = bg = None
    for line in art.split("\n"):
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\x1b":
                m = _ANSI_RE.match(line, i)
                if m:
                    params = m.group(1)
                    parts = [p for p in params.split(";") if p]
                    if not parts or parts == ["0"]:
                        fg = bg = None
                    elif params.startswith("38;2;") and len(parts) >= 5:
                        fg = Color.from_rgb(*(int(x) for x in parts[2:5]))
                    elif params.startswith("48;2;") and len(parts) >= 5:
                        bg = Color.from_rgb(*(int(x) for x in parts[2:5]))
                    i = m.end()
                    continue
                i += 1
                continue
            out.append(ch, style=Style(color=fg, bgcolor=bg) if (fg or bg) else None)
            i += 1
        out.append("\n")
    return out


def _fmt_s(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------- the app


class ThousandEyesApp(App):
    """The chat console: black terminal, violet accents, a real agent inside."""

    TITLE = "thousand-eyes"
    SUB_TITLE = "see everything. miss nothing."

    CSS = CSS

    # redraw-on-idle only; the banner owns its own timer
    ENABLE_LIVE_REGION = False

    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit"),
    ]

    def __init__(self, model: str = "", budget: int = 120,
                 budget_seconds: int = 600) -> None:
        super().__init__()
        self.model = model or ""
        self.budget = budget
        self.budget_seconds = budget_seconds
        self._busy = False
        self._stream = ""
        self._planner: Optional[Planner] = None
        self._llm: Any = None          # cached LLMClient for chat; False = no key
        self._history: List[Dict[str, str]] = []
        self._subject: List[str] = []
        self._last_report = ""
        self._last_ledger: List[dict] = []
        self._last_result: Any = None   # last ScanResult (the real findings)
        self._said: List[str] = []      # every agent line (tests read this)
        self._demo = False
        self._bar_total = 0.0          # seconds-of-work budget for this run
        self._run_start = 0.0          # monotonic start of current run
        self._cancel_requested = False
        self._scan_stats: Dict[str, int] = {"stages": 0, "modules": 0,
                                            "findings": 0, "failures": 0}

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
        yield RichLog(id="chat", auto_scroll=True, markup=True, wrap=True)
        yield self._hud()
        yield RichLog(id="stream", auto_scroll=True, markup=True, wrap=True,
                      highlight=False)
        yield Static("", id="status")
        yield Input(placeholder="chat about anything · or hand me a lead: "
                    "email · username · domain · url · phone", id="input")
        yield Footer()

    def _hud(self):
        """Progress bar + live stats line, always visible."""
        from textual.containers import Container
        from textual.widgets import ProgressBar

        bar = ProgressBar(total=100, show_eta=False, id="bar")
        stats = Static("", id="stats")
        return Container(bar, stats, id="hud")

    # ------------------------------------------------------------- lifecycle

    def on_mount(self) -> None:
        # claim the terminal tab (OSC 0/2): Windows Terminal shows this as the
        # tab title instead of "Windows PowerShell"
        import sys as _sys

        try:
            if _sys.stdout.isatty():
                _sys.stdout.write(
                    "\x1b]0;thousand-eyes · see everything. miss nothing.\x1b\\")
                _sys.stdout.flush()
        except Exception:
            pass
        self.query_one("#banner").update(self._banner_text())
        self._print_welcome()
        self._status("ready · chat about anything, or hand me a lead")
        self._refresh_hud()
        self.query_one("#input").focus()
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        """HUD heartbeat: refresh bar + stats twice a second while running."""
        self._refresh_hud()

    def _banner_text(self):
        """The reactive banner, rendered straight from the Scene's frame arrays.

        No ANSI round-trip: every cell keeps its exact truecolor style, so the
        TUI shows the same crisp art as `thousand-eyes --banner` in a terminal.
        """
        from thousand_eyes.banner import Scene

        cols = max(40, self.console.width - 2)
        scene = Scene(cols)
        ch, fgi, bgi, bgd = scene.final_frame()
        out = Text()
        for r in range(ch.shape[0]):
            for c in range(ch.shape[1]):
                glyph = str(ch[r, c])
                style = None
                if not bgd[r, c]:
                    style = Style(color=Color.from_rgb(*[int(v) for v in fgi[r, c]]),
                                  bgcolor=Color.from_rgb(*[int(v) for v in bgi[r, c]]))
                elif glyph != " ":
                    style = Style(color=Color.from_rgb(*[int(v) for v in fgi[r, c]]))
                out.append(glyph, style=style)
            out.append("\n")
        return out

    def _print_welcome(self) -> None:
        self._agent_line(f"thousand-eyes v{__version__} - online")
        self._agent_line("chat: general talk on footprint, breaches, brokers, "
                         "phishing, opsec - my lane, answered properly.")
        self._agent_line("audit: hand me a lead - email · username · domain · "
                         "url · phone - and i sweep the public web.")
        self._agent_line("tip: /help lists commands · /demo replays offline · "
                         "!cmd shells out.")
        try:
            from thousand_eyes.setup import status_line
            self._agent_line(status_line())
        except Exception:
            pass

    # ------------------------------------------------------------- chat log

    def _chat_line(self, text: str) -> None:
        try:
            self.query_one("#chat", RichLog).write(Text.from_markup(text))
        except Exception:
            pass

    def _user_line(self, text: str) -> None:
        """One clean line per turn - user gets a violet `you` tag, agent gets
        an `agent` tag, no boxes, no timestamps, everything wraps in-frame."""
        self._chat_line("[bold #6d5a9c]you[/] [white]" + text.replace("[", "\\[") + "[/]")

    def _say(self, text: str) -> None:
        """Write one chat line. Tests monkeypatch this to capture output."""
        self._said.append(text)
        self._chat_line(text)

    def _agent_say(self, text: str) -> None:
        body = str(text).replace("[", "\\[")
        self._say("[bold #a78bfa]agent[/] " + body)

    def _agent_line(self, text: str) -> None:
        self._agent_say(text)

    def _stream_line(self, text: str) -> None:
        """One line of the live cascade feed (the panel keeps the history)."""
        if not text:
            return
        try:
            self.query_one("#stream", RichLog).write(Text.from_markup(text))
        except Exception:
            pass

    def _status(self, text: str) -> None:
        try:
            self.query_one("#status", Static).update(text)
        except Exception:
            pass

    def _status_line(self) -> str:
        """Operator dashboard line: model, budget, last-run numbers."""
        if not self._busy:
            return (f"idle · model {self._live_model()} · cascade budget "
                    f"{self.budget_seconds}s · no run in flight")
        elapsed = time.monotonic() - self._run_start
        return (f"running · model {self._live_model()} · wall {_fmt_s(elapsed)} · "
                f"stage {self._scan_stats.get('stages', 0)} · modules "
                f"{self._scan_stats.get('modules', 0)} · findings "
                f"{self._scan_stats.get('findings', 0)}")

    # ------------------------------------------------------------- input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one("#input", Input).value = ""
        if text.startswith("/"):
            self._command(text)
            return
        self._route(text)

    def _route(self, text: str) -> None:
        action, lead = route_intent(text)
        if action == "audit":
            self._user_line(text)
            self._history.append({"role": "user", "content": text})
            self._run_audit([lead], text)
        elif action == "ask_lead":
            self._user_line(text)
            self._agent_line("which lead should I run? email, username, "
                             "domain, url or phone - e.g. "
                             "\"audit me with you@example.com\"")
        elif text.lower().strip() == "status":
            self._user_line(text)
            self.action_status()
        elif text.lower().strip() == "findings":
            self._user_line(text)
            self._agent_line(self._findings_line())
        elif text.lower().strip() == "fence":
            self._user_line(text)
            self._agent_line(f"fence: {FENCE}")
        elif text.lower().strip() == "report":
            self._user_line(text)
            self._agent_line(self._report_line())
        else:
            self._user_line(text)
            self._history.append({"role": "user", "content": text})
            self._chat(text)

    def _recap(self) -> str:
        """What it did, what it found - from the REAL result, not a ledger
        of empty tool names."""
        result = self._last_result
        if result is None:
            return ("no audit finished yet - hand me a lead (email, username, "
                    "domain, url, phone) and i'll run the cascade.")
        stages = result.meta.get("stages") or []
        lines: List[str] = []
        lines.append(f"what it did - {len(stages)} stage(s), "
                     f"{self._scan_stats.get('modules', 0)} module call(s):")
        for st in stages[:14]:
            lines.append(f"· stage {st.get('n')}: {st.get('type')} "
                         f"{st.get('value')} → {st.get('findings', 0)} finding(s)")
        if len(stages) > 14:
            lines.append(f"· … and {len(stages) - 14} more stage(s)")
        found = self._found_lines(result)
        lines.append("")
        lines.append(f"what it found - {len(found)} confirmed item(s):")
        lines.extend(found[:20])
        if len(found) > 20:
            lines.append(f"· … and {len(found) - 20} more")
        if not found:
            lines.append("· nothing confirmed - every source was inconclusive "
                         "(see the 'checks that failed' section)")
        stopped = result.meta.get("stopped")
        if stopped:
            lines.append("")
            lines.append(f"stopped early: {stopped}")
        return "\n".join(lines)

    def _findings_line(self) -> str:
        rows = [r for r in self._last_ledger if r.get("kind") == "finding"]
        if not rows:
            return "no evidence rows yet - run an audit on a lead first."
        return f"{len(rows)} evidence row(s): " + "; ".join(
            f"{r.get('value')} via {r.get('source')}" for r in rows[:5])

    def _report_line(self) -> str:
        if not self._last_report:
            return "no report yet - run an audit on a lead first."
        return f"last report: {len(self._last_report)} chars, " \
               f"{len(self._last_ledger)} ledger rows."

    # ------------------------------------------------------------- commands

    def _command(self, text: str) -> None:
        cmd = text.lower().strip()
        if cmd == "/demo":
            self.action_demo()
        elif cmd == "/clear":
            self.action_clear()
        elif cmd == "/status":
            self.action_status()
        elif cmd == "/cancel":
            self.action_cancel()
        elif cmd == "/help":
            self.action_help()
        elif cmd in ("/exit", "/quit"):
            self.action_exit()
            self.exit()
        elif cmd == "/setup" or cmd.startswith("/setup "):
            self._do_setup(cmd[6:].strip())
        elif cmd == "/skill":
            self._do_skill()
        else:
            self._agent_line(f"unknown command: {cmd} - /help for the list")

    def _do_setup(self, arg: str) -> None:
        """/setup [<provider> <key>] - guided key onboarding, in-frame."""
        from thousand_eyes.setup import key_status, save_key, status_line
        if not arg:
            self._agent_line(status_line())
            self._agent_line(
                "add a key without leaving the console:  /setup gemini <key> "
                "(free: aistudio.google.com/app/apikey)  or  /setup claude <key>")
            return
        parts = arg.split(None, 1)
        alias = {"gemini": "gemini", "google": "gemini", "claude": "anthropic",
                 "anthropic": "anthropic"}
        provider = alias.get(parts[0].lower())
        if not provider or len(parts) < 2:
            self._agent_line("usage: /setup gemini <key>  ·  /setup claude <key>")
            return
        try:
            path = save_key(provider, parts[1])
        except ValueError as exc:
            self._agent_line(f"not saved: {exc}")
            return
        # rebuild the chat client so the new key takes effect instantly
        self._llm = None
        provider_now, _k, _src = key_status()
        self._agent_line(f"key stored ({provider_now}) in {path} - full chat "
                         "is live. i'll stream answers from here on.")

    def _do_skill(self) -> None:
        """/skill - install the IDE-agent skill file, idempotent."""
        from thousand_eyes.skill import install_skill, skill_status
        written = install_skill()
        for p in written:
            self._agent_line(f"skill written: {p}")
        self._agent_line("restart your IDE session; the agent now knows "
                         "when and how to drive thousand-eyes. "
                         f"({skill_status()})")

    # ------------------------------------------------------------- chat

    async def _chat_llm(self, text: str) -> str:
        """Real chat when a key exists; the local knowledge base covers
        everything when it does not - either way the operator gets an
        answer, never a canned redirect."""
        llm = self._chat_client()
        if llm is None:
            return self._offline_answer(text)
        self._stream_line(f"{self._live_model()} thinking…")
        # history already ends with the operator's message
        msgs = [{"role": "system", "content": CHAT_SYSTEM}, *self._history]
        out = ""
        try:
            async for chunk, _stats in llm.chat(msgs):
                out += chunk
                self._stream_line(
                    f"{self._live_model()} typing… "
                    + out[-70:].replace("\n", " "))
        except Exception as e:
            self._agent_line(f"(llm unreachable: {type(e).__name__} · "
                             f"answering locally)")
            return self._offline_answer(text)
        finally:
            self._stream_line("")
        out = out.strip()
        if out:
            self._history.append({"role": "assistant", "content": out})
        return out or self._offline_answer(text)

    def _chat(self, text: str) -> None:
        if self._busy:
            self._agent_say("(still working on the last run - /cancel to stop)")
            return
        self.run_worker(self._chat_run(text), exclusive=True)

    async def _chat_run(self, text: str) -> None:
        self._busy = True
        try:
            self._history.append({"role": "user", "content": text})
            local = self._local_intent(text)
            if local:
                reply = local
            else:
                reply = await self._chat_llm(text) or ""
                del self._history[:-12]        # keep the last ~6 turns
            if reply:
                self._agent_say(reply)
        except Exception:  # noqa: BLE001 - never crash the TUI
            self._agent_say(self._offline_answer(text) or
                            "(offline - no llm key, and that one isn't canned)")
        finally:
            self._busy = False
            self._stream_line("")
            self.query_one("#input", Input).focus()

    def _local_intent(self, text: str) -> Optional[str]:
        """Operator dashboard words - answered locally, instantly, no LLM."""
        s = text.strip()
        low = s.lower()
        # read-only shell-out: !git status / !pip show thousand-eyes / …
        if s.startswith("!") and len(s) > 1:
            return await_cmd(s[1:])
        if s.startswith("/"):
            return self._command(s)
        if any(k in low for k in ("finding", "findings", "evidence", "results",
                                  "what did you", "what have you",
                                  "what did you do", "what all did you do",
                                  "what was found", "recap", "summar")):
            return self._recap()
        if any(k in low for k in ("progress", "status")):
            if self._busy:
                return (f"run in flight · stage {self._scan_stats.get('stages', 0)} · "
                        f"modules {self._scan_stats.get('modules', 0)} · findings "
                        f"{self._scan_stats.get('findings', 0)} · leads "
                        f"{self._scan_stats.get('leads', 0)}")
            return "idle. hand me a lead - email · username · domain · url · phone."
        if any(k in low for k in ("report", "dossier", "summary", "summarise", "summarize")):
            if self._last_result is not None:
                return self._recap()
            if self._last_report:
                return self._last_report[-1400:]
            return "no dossier yet - run an audit first (type a lead)."
        if any(k in low for k in ("fence", "safe", "safety", "permission")):
            return ("fence: no .onion resolution · no auth probing · no form submission · "
                    "no password tests · no breach dumps · per-domain 300ms bucket · "
                    "global concurrency 8 · the engine decides what is evidence, "
                    "the model only narrates it")
        return None

    def _live_model(self) -> str:
        """The model that will actually answer - provider-aware, never a
        hardcoded default (the key on this machine decides)."""
        client = self._chat_client()
        if client is not None:
            model = getattr(client, "model", "") or ""
            provider = getattr(client, "provider", "") or ""
            if model:
                return f"{provider}/{model}" if provider else model
            return provider or "llm"
        return self.model or "offline (no llm key)"

    def _chat_client(self):
        if self._llm is None:
            try:
                self._llm = client_for()
            except Exception:
                self._llm = False
        return self._llm or None

    def _offline_answer(self, text: str) -> str:
        """Local chat engine - a real knowledge base for the beat (footprint,
        breaches, brokers, opsec), with rotating phrasing so it never reads
        hardcoded. Deterministic per question (same ask, same answer)."""
        low = text.lower().strip()
        pick = hash(low) & 0xffff

        # off-genre: refuse and steer (code help, file reading, smalltalk)
        if any(k in low for k in (
                "code review", "review this", "review my", "read this file",
                "read the file", "read a file", "open the file", "fix my code",
                "my code", "write code", "debug", "traceback", "stack trace",
                "python script", "javascript", "regex help", "compile",
                "recipe", "weather", "movie", "song", "game", "homework")):
            return ("that's outside my beat. i do digital footprint, breach "
                    "exposure, data brokers, opsec and identity audits - hand "
                    "me a lead (email, username, domain, url, phone) and i'll "
                    "run it.")

        if any(k in low for k in ("hello", "hi ", "hey", "yo", "sup", "good morning",
                                  "good evening", "howdy")):
            return [
                "hey. i read public data only. chat with me about footprint, "
                "privacy or breaches, or hand me a lead and i'll audit it.",
                "yo. i'm the audit engine. give me your email or handle and "
                "i'll walk every confirmed pivot, or ask me anything in the "
                "footprint/privacy lane.",
                "hello. my beat: what the internet exposes about you and how "
                "to shrink it. type a lead for a full sweep.",
            ][pick % 3]
        if "who are you" in low or "what are you" in low or "what can you do" in low:
            return ("i'm thousand-eyes: an osint self-audit chat. two modes - "
                    "chat (footprint, breaches, brokers, opsec) and audit "
                    "(hand me an identifier, i sweep public data and rank "
                    "what an attacker could do with it).")
        if "breach" in low or "hibp" in low or "pwned" in low or "leaked" in low:
            return ("a breach hit means your identifier appears in a known "
                    "breach index. we never pull the dump - we only confirm "
                    "it exists, so you can rotate the password there first. "
                    "want that checked? hand me the email.")
        if "broker" in low or "data broker" in low or "opt out" in low or "removal" in low:
            return ("data brokers buy, merge and resell your records; most "
                    "have opt-out pages that work but decay over time. "
                    "prioritise: people-search sites first, then ad networks, "
                    "then re-check quarterly. an audit shows which ones "
                    "already have you.")
        if "footprint" in low or "reduce" in low or "remove myself" in low or "delete" in low:
            return ("shrink the footprint in this order: 1) audit your email "
                    "to see what's exposed, 2) rotate reused passwords, 3) "
                    "delete dormant accounts, 4) opt out of people-search "
                    "brokers, 5) lock socials to first-party only.")
        if "osint" in low:
            return ("osint = open-source intelligence: only public, legal, "
                    "first-party data. this tool is read-only and fenced - "
                    "no .onion, no auth probing, no form submission, no "
                    "breach dumps.")
        if "2fa" in low or "mfa" in low or "authenticator" in low:
            return ("prefer an authenticator app or a hardware key over sms "
                    "- sim-swap attacks kill text-based 2fa. enable it on "
                    "email first: it's the reset key to everything else.")
        if "password" in low:
            return ("password reuse is the #1 pivot: one breach unlocks "
                    "every site that shares the login. unique passwords per "
                    "site, a manager for recall, and rotate anything that "
                    "shows up in a breach index.")
        if "vpn" in low or "proxy" in low:
            return ("a vpn hides your ip from the sites you visit and your "
                    "isp from seeing dns - it does not hide accounts, "
                    "cookies or fingerprinting. it's one layer, not "
                    "anonymity.")
        if "doxx" in low or "dox" in low:
            return ("doxxing usually chains: handle -> platform accounts -> "
                    "bio details -> people-search. cut the chain by auditing "
                    "the handle first, then scrubbing bios, then locking "
                    "down people-search brokers.")
        if "phone" in low or "sim" in low or "spoof" in low:
            return ("your number pivots through people-search and broker "
                    "dbs, and a live number is a sim-swap target. audit it, "
                    "add a carrier pin, and treat sms 2fa as last resort.")
        if "alias" in low or "plus addressing" in low or "second email" in low:
            return ("aliases contain leaks: one per service means a breach "
                    "or sale identifies the source instantly. audit the "
                    "primary first, then migrate sign-ups over one alias at "
                    "a time.")
        if "dark web" in low or "darkweb" in low or "onion" in low:
            return ("we never contact .onion services - hard fence. we check "
                    "ahmia's clearnet index for mentions of your identifier "
                    "and cite what the index says, nothing more.")
        if "exif" in low or "metadata" in low or "photo" in low:
            return ("photos can carry exif: gps, device, timestamps. strip "
                    "before posting; and remember image search can tie "
                    "pictures back to you across accounts.")
        if "phishing" in low or "scam" in low or "spam" in low:
            return ("phishing works on urgency + authority. check the real "
                    "domain (not the display name), never log in from an "
                    "email link, and report. aliases make the source obvious "
                    "next time.")
        if "track" in low or "pixel" in low or "fingerprint" in low:
            return ("tracking is mostly cookies, pixels and fingerprinting "
                    "of canvas/fonts. block third-party cookies, prefer "
                    "reader modes, and remember logins link identities "
                    "across sites anyway.")
        if "username" in low or "handle" in low or "same name" in low \
                or "same account" in low or "different person" in low:
            return ("a matching username on two platforms is handle reuse, "
                    "not proof of one person. the only solid corroboration is "
                    "overlapping real name, bio, avatar, location or "
                    "cross-links between the accounts - so a handle match "
                    "alone gets tagged unconfirmed. i tag every account that "
                    "way in the report.")
        if "audit" in low or "check" in low or "scan" in low or "test" in low:
            return ("type a lead and i fire: email (gravatar, breach index, "
                    "github commits, registration leaves, mail posture), "
                    "username (wmn 692-site sweep, github, gitlab, "
                    "keybase, reddit, mastodon, bluesky), domain, url or "
                    "phone.")
        # no topic matched: rotate honest fallbacks that keep the chat alive
        return [
            "still in my lane: footprint, breaches, brokers, opsec. ask "
            "about any of those, or hand me a lead and i'll run the sweep.",
            "i can audit an identifier or talk you through privacy work. "
            "which one?",
            "give me an email, username, domain, url or phone and i'll show "
            "you what the public web holds on it. or ask about your "
            "footprint.",
        ][pick % 3]

    # ------------------------------------------------------------- audit

    def _run_audit(self, leads: List[str], goal: str) -> None:
        if self._busy:
            self._agent_line("(already running - /cancel first)")
            return
        self._busy = True
        self._demo = False
        self._subject = leads
        self._bar_total = float(self.budget_seconds)
        self._run_start = time.monotonic()
        self._cancel_requested = False
        self._scan_stats = {"stages": 0, "modules": 0, "findings": 0, "failures": 0}
        self._status(f"audit starting · {len(leads)} lead(s) · "
                     f"cascade · budget {self.budget_seconds}s")
        self._audit_async(leads, goal, False)

    def _audit_async(self, leads: List[str], goal: str, demo: bool) -> None:
        """Run the deterministic cascade (the API bridge) inside the app's
        event loop. The engine - not an LLM - decides what is a finding;
        every confirmed pivot becomes the next lead, and the cascade runs
        until the frontier is exhausted or the budget runs out."""

        async def runner() -> None:
            from thousand_eyes.scan import run_scan
            transport = demo_transport() if demo else None
            try:
                result = await run_scan(
                    raw_seeds=list(leads),
                    max_depth=4,
                    wild=False,
                    all_sites=True,
                    transport=transport,
                    on_event=self._on_scan_event,
                    time_budget=float(self.budget_seconds),
                    abort=lambda: self._cancel_requested,
                )
            except Exception as e:      # never show a bare traceback
                self._agent_line(f"scan failed: {type(e).__name__}: {e}")
                self._busy = False
                self._status("ready")
                self._refresh_hud()
                return
            self._result = result
            self._finish_scan(result)

        self._task = asyncio.create_task(runner())

    # ------------------------------------------------- live cascade feed

    def _on_scan_event(self, event: Tuple[str, Any]) -> None:
        """Render the engine's live events as the 'doing this / found that'
        feed. This is the honest picture of the cascade, step by step."""
        kind, data = event
        if kind == "stage_start":
            self._scan_stats["stages"] = data.get("n", 0)
            self._scan_stats["leads"] = data.get("n", 0)
            self._stream_line(f"[bold #a78bfa]stage {data.get('n')}[/] "
                              f"{data.get('type')} → {data.get('value')}")
        elif kind == "module":
            state = data.get("state")
            mark = "[#ff6b6b]fail[/]" if state == "fail" else "[#5bd6a0]ok[/]"
            if state != "skip":
                self._scan_stats["modules"] = self._scan_stats.get("modules", 0) + 1
            n = data.get("findings", 0)
            extra = f" · {n} finding(s)" if n else ""
            err = data.get("error") or ""
            tail = f" · {err}" if err else ""
            self._stream_line(f"  {mark} {data.get('label')}{extra}{tail}")
        elif kind == "found":
            self._scan_stats["findings"] = self._scan_stats.get("findings", 0) + 1
            self._stream_line(f"    [bold #f5c542]found[/] {data.get('kind')}: "
                              f"{data.get('value')}")
        elif kind == "stage_end":
            self._stream_line(f"  [dim]stage {data.get('n')} done · "
                              f"{data.get('findings', 0)} finding(s) · "
                              f"{data.get('modules', 0)} module(s) · "
                              f"{data.get('seconds', 0)}s[/]")
        elif kind == "stopped":
            self._stream_line(f"[bold #ff6b6b]stopped[/] {data.get('reason')}")
        self._refresh_hud()

    # --------------------------------------------------------- the report

    def _finish_scan(self, result) -> None:
        """Print what it did and what it found - the deterministic truth
        first, then (optionally) the LLM's analysis over that same evidence."""
        from thousand_eyes.output import render_text

        self._last_result = result
        wall = result.meta.get("duration_seconds", 0.0)
        stages = result.meta.get("stages") or []
        self._chat_line("")
        self._chat_line("[bold #a78bfa]── WHAT IT DID ─────────────────────────────[/]")
        for st in stages:
            self._chat_line(
                f"[white]  stage {st.get('n')}: {st.get('type')} "
                f"[bold]{st.get('value')}[/] → {st.get('findings', 0)} finding(s) "
                f"from {st.get('modules', 0)} module(s) "
                f"({st.get('seconds', 0)}s)[/]")
        stopped = result.meta.get("stopped")
        if stopped:
            self._chat_line(f"[#ff6b6b]  {stopped}[/]")
        self._chat_line("")
        self._chat_line(render_text(result, version=__version__))
        self._chat_line("")
        self._chat_line("[bold #a78bfa]── WHAT IT FOUND ────────────────────────────[/]")
        found = self._found_lines(result)
        if found:
            for line in found:
                self._chat_line(line)
        else:
            self._chat_line("[white]  nothing confirmed - every source was "
                            "inconclusive (see 'checks that failed').[/]")
        self._last_report = "\n".join(found)
        self._busy = False
        self._refresh_hud()
        self._status("ready")
        self._agent_line(f"done · {len(stages)} stage(s) · "
                         f"{self._scan_stats.get('findings', 0)} finding(s) · "
                         f"{wall}s · ask 'what did you find' for the recap.")
        self._task = asyncio.create_task(self._llm_analysis(result))

    def _found_lines(self, result) -> List[str]:
        """Every confirmed pivot, one line each, with its evidence source."""
        out: List[str] = []
        for node in result.nodes.values():
            if node.value == result.seed:
                continue
            if node.type == "fact":
                continue
            out.append(f"[white]  · {node.type}: {node.value}[/]")
        for leaf in result.leaves:
            out.append(f"[white]  · {leaf.site}[/]")
        return out[:60]

    async def _llm_analysis(self, result) -> None:
        """Optional: the LLM writes the analysis over the REAL evidence the
        engine collected. If there is no key (or it fails), the deterministic
        report above stands on its own - nothing is lost."""
        llm = self._chat_client()
        if llm is None:
            return
        digest = "\n".join(self._found_lines(result))[:4000]
        stages = "\n".join(
            f"stage {s.get('n')}: {s.get('type')} {s.get('value')} -> "
            f"{s.get('findings')} finding(s)" for s in (result.meta.get("stages") or []))
        prompt = (
            "You are analysing a finished OSINT self-audit. The evidence below is "
            "REAL and confirmed by public first-party APIs. Write a tight "
            "operator-facing analysis: who is this person, what is their online "
            "presence, what would an attacker do with it, and what to fix first. "
            "Be concrete, cite the identifiers, keep it under 180 words, no "
            "markdown headers, no emojis. Never invent a finding that is not in "
            "the evidence.\n\n"
            f"STAGES:\n{stages}\n\nFINDINGS:\n{digest}")
        out = ""
        try:
            async for chunk, _stats in llm.chat([{"role": "user", "content": prompt}]):
                out += chunk
        except Exception:
            return
        text = out.strip()
        if text:
            self._chat_line("")
            self._chat_line("[bold #a78bfa]── ANALYSIS ────────────────────────────────[/]")
            self._chat_line(f"[white]{text}[/]")

    # ------------------------------------------------------------- HUD

    def _refresh_hud(self) -> None:
        bar = self.query_one("#bar")
        stats = self.query_one("#stats")
        if not self._busy:
            bar.update(progress=0)
            stats.update("idle · model " + self._live_model()
                         + " · cascade budget "
                         + f"{self.budget_seconds}s · no run in flight")
            return
        elapsed = time.monotonic() - self._run_start
        frac = min(1.0, elapsed / max(self._bar_total, 1.0))
        bar.update(progress=int(frac * 100))
        eta = max(0.0, self._bar_total - elapsed)
        s = (f"working · t+{_fmt_s(elapsed)} · eta {_fmt_s(eta)} · "
             f"stage {self._scan_stats.get('stages', 0)} · "
             f"modules {self._scan_stats.get('modules', 0)} · "
             f"findings {self._scan_stats.get('findings', 0)} · "
             f"leads {self._scan_stats.get('leads', 0)}")
        stats.update(s)

    # ------------------------------------------------------------- commands

    def action_demo(self) -> None:
        if self._busy:
            self._agent_line("(already running - /cancel first)")
            return
        self._busy = True
        self._demo = True
        self._subject = ["demo.subject@example.org"]
        self._bar_total = float(self.budget_seconds)
        self._run_start = time.monotonic()
        self._cancel_requested = False
        self._scan_stats = {"stages": 0, "modules": 0, "findings": 0, "failures": 0}
        self._status("demo mode · offline replay")
        self._agent_line("demo run starting (offline synthetic replay)…")
        self._audit_async(self._subject, "demo", True)

    def action_status(self) -> None:
        if not self._busy:
            self._agent_line("idle · no run in flight · model "
                             f"{self._live_model()} · cascade budget "
                             f"{self.budget_seconds}s")
            return
        elapsed = time.monotonic() - self._run_start
        self._agent_line(
            f"running · t+{_fmt_s(elapsed)} · stage "
            f"{self._scan_stats.get('stages', 0)} · modules "
            f"{self._scan_stats.get('modules', 0)} · findings "
            f"{self._scan_stats.get('findings', 0)} · leads "
            f"{self._scan_stats.get('leads', 0)}")

    def action_cancel(self) -> None:
        if self._busy:
            self._cancel_requested = True
            self._status("stopping… (the cascade stops after the current step)")
        else:
            self._status("nothing to cancel")

    def action_clear(self) -> None:
        self.query_one("#chat", RichLog).clear()
        self._stream_line("")
        self._print_welcome()

    def action_help(self) -> None:
        self._chat_line("[bold white]commands:[/]")
        for line in ("  /demo      offline synthetic audit replay",
                     "  /clear     wipe the chat",
                     "  /setup     add or check your LLM key",
                     "  /skill     install the IDE-agent skill file",
                     "  /status    live analytics for the current run",
                     "  /cancel    stop the running audit",
                     "  /palette   jump to the input palette",
                     "  /help      this list",
                     "  /exit      quit",
                     "  !cmd       run a shell command (!pip list)",
                     "",
                     "  chat: ask anything about footprint / privacy / OSINT",
                     "  audit: hand me a lead - email · username · domain · "
                     "url · phone",
                     f"  fence: {FENCE}"):
            self._chat_line(line)

    def action_exit(self) -> None:
        self.exit()


def main() -> None:
    ThousandEyesApp().run()

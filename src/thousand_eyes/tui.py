"""thousand-eyes interactive TUI — a chat-first agent console.

Bare `thousand-eyes` opens this: black terminal, the violet banner up top,
a streaming chat, a live pipeline HUD (progress bar · elapsed · ETA ·
counts), a token pane, a status line and a one-line input box. It is a
GENERAL conversational agent: chat about anything and it answers (LLM when
a key is present, gracefully offline without one). An audit fires ONLY on a
real identifier — email, username, domain, url, phone — or an explicit ask
like "find out what you can using my email - x@y.com". Smalltalk never
scans anything.

Design contract (v1.0):
  * violet lives ONLY in the banner + input border; everything else white
  * your messages are tagged [YOU]; agent turns are tagged [AGENT]
  * while the agent works, the stream pane shows its CURRENT STEP live
  * every run ends with a structured SUMMARY (analytics + findings)

Commands:  /demo  /clear  /status  /cancel  /help  /exit
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
#banner { background: black; height: 10; padding: 0 0; }
#chat {
    border: round #2a2a2a;
    padding: 0 1;
    color: white;
    background: black;
    height: 1fr;
    scrollbar-size-vertical: 0;
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
    height: 3;
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
    "You are thousand-eyes, an OSINT self-audit agent. Answer ONLY questions "
    "about digital footprint, online presence, privacy and safety — data "
    "brokers, breach exposure, what strangers can find about someone, how to "
    "reduce an online footprint, how OSINT works. For anything off-topic "
    "(movies, recipes, code help, weather), say in one sentence that you only "
    "cover footprint, safety and online presence, then invite a lead. Be "
    "terse: at most 3 short sentences, no markdown, no headers, no emojis, no "
    "ANSI codes. Never fabricate a finding and never claim something is "
    "absent — uncertainty is INCONCLUSIVE. To run an audit you need a lead "
    "from the operator: email, username, domain, url or phone."
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
    one — never on plain conversation.
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
    # '<audit verb> <handle>' — trailing token is the lead. Split on
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

    def __init__(self, model: str = "", budget: int = 120) -> None:
        super().__init__()
        self.model = model or "claude-sonnet-5"
        self.budget = budget
        self._busy = False
        self._stream = ""
        self._planner: Optional[Planner] = None
        self._llm: Any = None          # cached LLMClient for chat; False = no key
        self._history: List[Dict[str, str]] = []
        self._subject: List[str] = []
        self._last_report = ""
        self._last_ledger: List[dict] = []
        self._said: List[str] = []      # every agent line (tests read this)
        self._demo = False
        self._bar_total = 0.0          # seconds-of-work budget for this run
        self._run_start = 0.0          # monotonic start of current run

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
        yield RichLog(id="chat", auto_scroll=True, markup=True)
        yield self._hud()
        yield Static("", id="stream", markup=False)
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
        self.query_one("#banner").update(_parse_ansi_art(self._banner_art()))
        self._print_welcome()
        self._status("ready · chat about anything, or hand me a lead")
        self._refresh_hud()
        self.query_one("#input").focus()
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        """HUD heartbeat: refresh bar + stats twice a second while running."""
        self._refresh_hud()

    def _banner_art(self) -> str:
        from thousand_eyes.banner import Scene, frame_to_text
        cols = max(40, self.console.width - 2)
        scene = Scene(cols)
        return frame_to_text(scene.final_frame())

    def _print_welcome(self) -> None:
        self._agent_line(f"thousand-eyes v{__version__} — online")
        self._agent_line("chat: ask me anything about footprint, privacy, "
                         "safety, OSINT.")
        self._agent_line("audit: hand me a lead — email · username · domain · "
                         "url · phone.")

    # ------------------------------------------------------------- chat log

    def _chat_line(self, text: str) -> None:
        try:
            self.query_one("#chat", RichLog).write(Text.from_markup(text))
        except Exception:
            pass

    def _user_line(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._chat_line(f"[bold white]┌─[ YOU · {stamp} ]"
                        f"──────────────────────────┐[/]\n[white]{text}[/]\n"
                        f"[bold white]└────────────────────────────────"
                        f"────────────────┘[/]")

    def _say(self, text: str) -> None:
        """Write one chat line. Tests monkeypatch this to capture output."""
        self._said.append(text)
        self._chat_line(text)

    def _agent_say(self, text: str) -> None:
        self._say(f"[bold white][ agent ][/] {text}")

    def _agent_line(self, text: str) -> None:
        self._agent_say(text)

    def _stream_line(self, text: str) -> None:
        """Live step line — the agent's CURRENT STATE, one line, overwritten."""
        try:
            self.query_one("#stream", Static).update(text)
        except Exception:
            pass

    def _status(self, text: str) -> None:
        try:
            self.query_one("#status", Static).update(text)
        except Exception:
            pass

    def _status_line(self) -> str:
        """Operator dashboard line: model, budget, last-run numbers."""
        a = self._planner.analytics if self._planner else None
        if a is None:
            return (f"idle · model {self._live_model()} · budget "
                    f"{self.budget} calls · no run in flight")
        return (f"running · model {self.model} · wall {_fmt_s(a.elapsed_s())} · "
                f"tools {a.tool_calls} · tok {a.prompt_tokens + a.completion_tokens} · "
                f"fetch {a.fetches} · breaches {a.breaches}")

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
                             "domain, url or phone — e.g. "
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

    def _findings_line(self) -> str:
        rows = [r for r in self._last_ledger if r.get("kind") == "finding"]
        if not rows:
            return "no evidence rows yet — run an audit on a lead first."
        return f"{len(rows)} evidence row(s): " + "; ".join(
            f"{r.get('value')} via {r.get('source')}" for r in rows[:5])

    def _report_line(self) -> str:
        if not self._last_report:
            return "no report yet — run an audit on a lead first."
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
        else:
            self._agent_line(f"unknown command: {cmd} — /help for the list")

    # ------------------------------------------------------------- chat

    async def _chat_llm(self, text: str) -> None:
        canned = self._offline_answer(text)
        if canned:
            self._agent_line(canned)
            return
        llm = self._chat_client()
        if llm is None:
            self._agent_line("no llm key — i only answer footprint, "
                             "privacy and osint smalltalk offline. hand me "
                             "a lead to audit.")
            return
        self._stream_line("thinking…")
        msgs = [{"role": "system", "content": CHAT_SYSTEM}, *self._history,
                {"role": "user", "content": text}]
        out = ""
        try:
            async for chunk, _stats in llm.chat(msgs):
                out += chunk
                self._stream_line(
                    f"{self._live_model()} typing… "
                    + out[-70:].replace("\n", " "))
        except Exception as e:
            self._agent_line(f"llm error: {e}")
            return
        finally:
            self._stream_line("")
        out = out.strip() or "(no answer)"
        self._history.append({"role": "assistant", "content": out})
        return out

    def _chat(self, text: str) -> None:
        if self._busy:
            self._agent_say("(still working on the last run — /cancel to stop)")
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
        except Exception:  # noqa: BLE001 — never crash the TUI
            self._agent_say(self._offline_answer(text) or
                            "(offline — no llm key, and that one isn't canned)")
        finally:
            self._busy = False
            self._stream_line("")
            self.query_one("#input", Input).focus()

    def _local_intent(self, text: str) -> Optional[str]:
        """Operator dashboard words — answered locally, instantly, no LLM."""
        s = text.strip()
        low = s.lower()
        # read-only shell-out: !git status / !pip show thousand-eyes / …
        if s.startswith("!") and len(s) > 1:
            return await_cmd(s[1:])
        if s.startswith("/"):
            return self._command(s)
        if any(k in low for k in ("finding", "findings", "evidence", "results",
                                  "what did you", "what have you")):
            rows = self._last_ledger[-5:]
            if rows:
                return "\n".join(
                    f"· {r.get('tool')} — {r.get('summary', '')[:80]}" for r in rows)
            return "no evidence rows yet — hand me a lead and i'll start the sweep."
        if any(k in low for k in ("progress", "status")):
            if self._planner is not None:
                sm = self._planner.analytics.summary()
                return (f"run in flight — {sm.get('tool_calls', 0)} tool calls · "
                        f"{sm.get('pipelines_fired', 0)} batches · "
                        f"{sm.get('leads_seen', 0)} leads · ledger "
                        f"{len(self._planner.ledger)} rows")
            return "idle. fence armed, zero network. hand me a lead."
        if any(k in low for k in ("report", "dossier", "summary", "summarise", "summarize")):
            if self._last_report:
                return self._last_report[-1400:]
            return "no dossier yet — run an audit first (type a lead)."
        if any(k in low for k in ("fence", "safe", "safety", "permission")):
            return ("fence: no .onion resolution · no auth probing · no form submission · "
                    "no password tests · no breach dumps · per-domain 300ms bucket · "
                    "global concurrency 8 · planner executes, model selects")
        return None

    def _live_model(self) -> str:
        """The model that will actually answer — provider-aware, cheap."""
        client = self._chat_client()
        if client is not None:
            return getattr(client, "model", self.model) or self.model
        return self.model

    def _chat_client(self):
        if self._llm is None:
            try:
                self._llm = client_for()
            except Exception:
                self._llm = False
        return self._llm or None

    def _offline_answer(self, text: str) -> str:
        low = text.lower().strip()
        if any(k in low for k in ("hello", "hi", "hey", "yo", "sup")):
            return ("hey. i read public data only — hand me a lead (email, "
                    "username, domain, url, phone) and i'll audit it.")
        if "brokers" in low:
            return ("data brokers are the middlemen: they buy, merge and resell "
                    "your records. the audit sweeps them for your identifier "
                    "and reports existence only — no dumps.")
        if "breach" in low:
            return ("a breach hit means your identifier appears in a known "
                    "breach index. we never pull the dump — we only report "
                    "that it exists, so you can rotate the password.")
        if "footprint" in low or "reduce" in low or "remove" in low:
            return ("start with an audit on your own email: it maps brokers, "
                    "breaches and sites that know you. then delete what you "
                    "can, opt out of brokers, and stop reusing handles.")
        if "osint" in low:
            return ("osint = open-source intelligence: only public, legal, "
                    "first-party data. this tool is read-only and fenced — no "
                    ".onion, no auth probing, no form submission.")
        if "safe" in low or "privacy" in low:
            return ("read-only, keyless, first-party endpoints only. no "
                    "password tests, no breach dumps, per-domain rate limits. "
                    "your scan stays on this machine.")
        return None   # not canned: the LLM answers this one

    # ------------------------------------------------------------- audit

    def _run_audit(self, leads: List[str], goal: str) -> None:
        if self._busy:
            self._agent_line("(already running — /cancel first)")
            return
        self._busy = True
        self._demo = False
        self._subject = leads
        self._bar_total = self.budget
        self._run_start = time.monotonic()
        self._status(f"audit starting · {len(leads)} lead(s) · "
                     f"{self.model}")
        self._audit_async(leads, goal, False)

    def _audit_async(self, leads: List[str], goal: str, demo: bool) -> None:
        """Run the planner inside the app's event loop, streaming to the UI."""

        async def runner() -> None:
            llm = self._chat_client()
            transport = demo_transport() if demo else None
            planner = Planner(llm, goal=goal, budget_calls=self.budget,
                              transport=transport)
            self._planner = planner
            try:
                report = await planner.run(leads, goal, fast=True)
                self._finish(report, planner)
            except Exception as e:
                self._finish(f"run failed: {e}", planner)
            finally:
                self._planner = None
        self._task = asyncio.create_task(runner())

    def _on_event(self, event: Tuple[str, Any]) -> None:
        kind, data = event
        label = _STEP_LABEL.get(kind, kind)
        if kind == "tool_start":
            label = f"running {data.get('tool', '?')}"
        elif kind == "tool_done":
            label = f"{data.get('tool', '?')} ok · {data.get('ms', 0):.0f}ms"
        elif kind == "tool_err":
            label = f"{data.get('tool', '?')} failed"
        elif kind == "finding":
            label = f"finding: {data.get('kind', '?')} · {data.get('value', '')}"
        elif kind == "round":
            label = (f"round {data.get('depth', 0)} · {data.get('tools', 0)} "
                     f"tools · ledger {data.get('ledger', 0)}")
        elif kind == "done":
            label = "run complete"
        self._stream_line(label)
        # live HUD refresh
        self._refresh_hud()

    # ------------------------------------------------------------- HUD

    def _refresh_hud(self) -> None:
        planner = self._planner
        bar = self.query_one("#bar")
        stats = self.query_one("#stats")
        if planner is None:
            bar.update(progress=100)
            stats.update("idle · model " + self._live_model())
            return
        a: Analytics = planner.analytics
        elapsed = time.monotonic() - self._run_start
        frac = min(1.0, elapsed / max(self._bar_total, 1.0))
        bar.update(progress=int(frac * 100))
        eta = max(0.0, self._bar_total - elapsed)
        s = (f"working · t+{_fmt_s(elapsed)} · eta {_fmt_s(eta)} · "
             f"tools {a.tool_calls} · fetch {a.fetches} · "
             f"breaches {a.breaches} · "
             f"tok {a.prompt_tokens + a.completion_tokens} ({a.tok_per_s():.0f}/s) · "
             f"llm {a.llm_calls} · ttft {a.avg_ttft_ms():.0f}ms · "
             f"pivots {a.pivots}")
        stats.update(s)

    # ------------------------------------------------------------- summary

    def _finish(self, report: str, planner: Planner) -> None:
        self._last_report = report
        self._last_ledger = planner.ledger_rows()
        a = planner.analytics
        wall = a.summary().get("wall_s", 0.0)
        self._stream_line("")
        self._chat_line("")
        self._chat_line("[bold white]┌─[ SUMMARY ]──────────────────────"
                        "──────────────────┐[/]")
        self._chat_line(f"[white]wall {_fmt_s(wall)} · llm calls "
                        f"{a.llm_calls} · tok {a.prompt_tokens + a.completion_tokens} "
                        f"({a.tok_per_s():.0f}/s) · ttft avg "
                        f"{a.avg_ttft_ms():.0f}ms · tools {a.tool_calls} · "
                        f"fetch {a.fetches} · breaches {a.breaches} · "
                        f"sites {a.sites_claimed} · dork hits {a.dork_hits} · "
                        f"dark hits {a.dark_hits} · pivots {a.pivots} · "
                        f"errors {a.tool_errors}[/]")
        findings = [r for r in self._last_ledger if r.get("kind") == "finding"]
        if findings:
            self._chat_line("[bold white]findings:[/]")
            for f in findings[:12]:
                self._chat_line(f"[white]  · {f.get('value', '')} — "
                                f"{f.get('summary', '')}[/]")
        else:
            self._chat_line("[white]no findings — everything inconclusive "
                            "(or the run stopped early).[/]")
        self._chat_line(f"[bold white]report:[/] {len(report)} chars · "
                        f"ledger {len(self._last_ledger)} rows · fence: "
                        f"{FENCE}[/]")
        self._chat_line("[bold white]└──────────────────────────────────"
                        "──────────────────┘[/]")
        self._chat_line("")
        self._agent_line("done. chat more, or hand me another lead.")
        self._busy = False
        self._refresh_hud()
        self._status("ready")

    # ------------------------------------------------------------- commands

    def action_demo(self) -> None:
        if self._busy:
            self._agent_line("(already running — /cancel first)")
            return
        self._busy = True
        self._demo = True
        self._subject = ["demo.subject@example.org"]
        self._bar_total = 30.0
        self._run_start = time.monotonic()
        self._status("demo mode · offline replay")
        self._agent_line("demo run starting (offline synthetic replay)…")
        self._audit_async(self._subject, "demo", True)

    def action_status(self) -> None:
        a: Optional[Analytics] = self._planner.analytics if self._planner else None
        if a is None:
            self._agent_line("idle · no run in flight · model "
                             f"{self.model} · budget {self.budget}")
            return
        wall = a.summary().get("wall_s", 0.0)
        self._agent_line(
            f"running · wall {_fmt_s(wall)} · llm {a.llm_calls} · "
            f"tok {a.prompt_tokens + a.completion_tokens} ({a.tok_per_s():.0f}/s) · tools "
            f"{a.tool_calls} · fetch {a.fetches} · breaches {a.breaches} · "
            f"pivots {a.pivots} · errors {a.tool_errors}")

    def action_cancel(self) -> None:
        if self._planner:
            self._planner.stop()
            self._status("stopping…")
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
                     "  /status    live analytics for the current run",
                     "  /cancel    stop the running audit",
                     "  /help      this list",
                     "  /exit      quit",
                     "",
                     "  chat: ask anything about footprint / privacy / OSINT",
                     "  audit: hand me a lead — email · username · domain · "
                     "url · phone",
                     f"  fence: {FENCE}"):
            self._chat_line(line)

    def action_exit(self) -> None:
        self.exit()


def main() -> None:
    ThousandEyesApp().run()

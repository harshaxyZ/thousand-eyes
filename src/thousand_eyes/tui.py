"""thousand-eyes interactive TUI — a chat-first agent console.

Bare `thousand-eyes` opens this: black terminal, the banner up top, a
streaming chat, a live pipeline HUD (progress bar · elapsed · ETA · counts),
a token pane, a status line and a one-line prompt. It is a GENERAL
conversational agent scoped to footprint/safety/online-presence: chat about
anything and it answers (LLM when a key is present, gracefully offline
without one). An audit fires ONLY on a real identifier — email, username,
domain, url, phone — or an explicit ask like "find out what you can using my
email - x@y.com". Smalltalk never scans anything.

Commands:  /demo  /clear  /status  /cancel  /help  /exit
"""

from __future__ import annotations

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
    "now", "then", "more", "again", "all", "some", "any", "it", "its", "me",
    "my", "your", "the", "and", "or", "to", "of", "in", "on", "for", "with",
    "about", "into", "over", "under", "once", "here", "there", "is", "are",
    "web", "dark", "deep", "net", "online", "internet", "people", "person",
    "someone", "anyone", "everyone", "him", "her", "them", "us", "you",
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

    CSS = """
    Screen {
        background: #000000;
        color: #d6d3d1;
    }
    Header {
        background: #0a0a0a;
        color: #a78bfa;
        text-style: bold;
    }
    Footer {
        background: #0a0a0a;
        color: #8b7bb0;
    }
    #banner {
        border: tall #3b1d63;
        padding: 0 1;
        background: #000000;
        height: 8;
        overflow: hidden;
    }
    #chat {
        border: round #3b1d63;
        padding: 0 1;
        background: #08080d;
        color: #e7e5f0;
        height: 1fr;
        scrollbar-size-vertical: 0;   # no scrollbar: everything fits on screen
    }
    #hud {
        border: round #241536;
        background: #000000;
        padding: 0 1;
        height: 3;
    }
    #bar {
        height: 1;
        width: 1fr;
        margin: 0;
    }
    #stats {
        height: 1;
        color: #a78bfa;
        background: #000000;
        text-style: bold;
    }
    #stream {
        border: round #241536;
        padding: 0 1;
        color: #7c6ba8;
        background: #000000;
        height: 3;
    }
    #status {
        color: #a78bfa;
        background: #0a0a0a;
        padding: 0 1;
        height: 1;
    }
    #input {
        border: tall #5b21b6;
        background: #0a0a0a;
        color: #f5f3ff;
    }
    #input > .input--placeholder {
        color: #5a4a6e;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit"),
    ]

    def __init__(self, model: str = "", budget: int = 120) -> None:
        super().__init__()
        self.model = model or "gemini-3.5-flash-lite"
        self.budget = budget
        self._busy = False
        self._stream = ""
        self._planner = None
        self._llm: Any = None    # cached LLMClient for chat; False = no key
        self._history: List[Dict[str, str]] = []
        self._subject: List[str] = []
        self._last_report = ""
        self._last_ledger: List[dict] = []
        self._demo = False
        self._bar_total = 0.0     # seconds-of-work budget for this run
        self._run_start = 0.0     # monotonic start of current run

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
        try:
            from textual.color import Gradient
            grad = Gradient.from_stops((0, "#5b21b6"), (1, "#c084fc"))
        except Exception:  # noqa: BLE001 — decoration, never fatal
            grad = None
        bar = ProgressBar(total=0, show_eta=False, show_percentage=False,
                          gradient=grad, id="bar")
        stats = Static("fence armed · idle · zero network", id="stats")
        return Container(bar, stats, id="hud")

    def on_mount(self) -> None:
        self._render_banner()
        self.set_interval(1.0, self._tick)
        self._say("[bold #a78bfa]THOUSAND EYES[/] v%s — interactive self-audit" % __version__)
        self._say("[#8b7bb0]talk to me about anything. audits fire only when you hand[/]")
        self._say("[#8b7bb0]me a lead — email · username · domain · url · phone.[/]")
        self._say("[#6d5a8f]/demo offline replay · /clear · /status · /cancel · /help · /exit[/]")
        self._say("[#6d5a8f]fence: no onion · no auth probing · no breach dumps — enforced in code[/]")
        self._set_status("fence armed · waiting for a lead")
        self.query_one("#input", Input).focus()

    def _render_banner(self) -> None:
        """Paint the banner as rich text at the widget's exact width."""
        try:
            from thousand_eyes.banner import Scene, frame_to_text
            w = max(40, self.size.width - 6)          # borders + padding + margin
            sc = Scene(min(w, 140), eye=True, small=True)
            art = _parse_ansi_art(frame_to_text(sc.final_frame()))
        except Exception:  # noqa: BLE001 — banner is decoration, never fatal
            art = Text("THOUSAND EYES", style="bold #a78bfa")
        self.query_one("#banner", Static).update(art)

    def on_resize(self, event) -> None:
        self._render_banner()

    # ------------------------------------------------------------- output

    def _say(self, text: str) -> None:
        self.query_one("#chat", RichLog).write(text)

    def _agent_say(self, text: str) -> None:
        self._say(f"[bold #a78bfa]agent[/] › {text}")

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    # ------------------------------------------------------------- HUD

    def _tick(self) -> None:
        """1s heartbeat: keep elapsed/ETA/progress live while a run is in flight."""
        if self._planner is not None:
            self._hud_live()

    def _hud_live(self) -> None:
        """Time-based bar (honest ETA by mode) + live counters from the ledger."""
        a = self._planner.analytics.summary() if self._planner else {}
        wall = time.monotonic() - self._run_start
        total = self._bar_total or 1.0
        self.query_one("#bar").update(total=total, progress=min(wall, total))
        eta = max(0.0, total - wall)
        stats = (f"t+{_fmt_s(wall)} · eta ~{_fmt_s(eta)}"
                 + f" · tools {a.get('tool_calls', 0)}"
                 + f" · ledger {self._ledger_rows()} rows"
                 + f" · sites {a.get('sites_claimed', 0)}"
                 + f" · breaches {a.get('breaches', 0)}"
                 + f" · pivots {a.get('pivots', 0)}"
                 + f" · {a.get('tok_per_s', 0)} tok/s"
                 + f" · working {_fmt_s(wall)} of ~{_fmt_s(total)}")
        if a.get("rate_limit_events"):
            stats += f" · rate-limits {a.get('rate_limit_events')}"
        self.query_one("#stats").update(stats)

    def _ledger_rows(self) -> int:
        pl = self._planner
        return len(pl.ledger) if pl is not None else len(self._last_ledger)

    def _hud_idle(self) -> None:
        self.query_one("#bar").update(total=0, progress=0)
        self.query_one("#stats").update("fence armed · idle · zero network")

    def _hud_done(self, a: Dict[str, Any]) -> None:
        wall = time.monotonic() - self._run_start
        if self._bar_total > 0:
            self.query_one("#bar").update(total=self._bar_total,
                                          progress=self._bar_total)
        self.query_one("#stats").update(
            f"done in {_fmt_s(wall)} · {a.get('tool_calls', 0)} tool calls · "
            f"{a.get('pivots', 0)} pivots · {a.get('sites_claimed', 0)} sites · "
            f"{a.get('breaches', 0)} breaches · ledger {self._ledger_rows()} rows"
            + (f" · rate-limits {a.get('rate_limit_events')}"
               if a.get("rate_limit_events") else ""))

    # ------------------------------------------------------------- input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._command(text[1:].strip())
            return
        self._say(f"[bold #c4b5fd]you[/] › {esc(text)}")
        action, payload = route_intent(text)
        if action == "audit":
            if self._busy:
                self._say("[#6d5a8f]agent is mid-run — /cancel to stop, or wait[/]")
                return
            self._subject = [payload]
            self._start_agent([payload])
        elif action == "ask_lead":
            self._agent_say(esc("hand me the lead to audit — email · username · "
                                "domain · url · phone (e.g. 'audit you@example.com')"))
        else:
            if self._busy:
                self._say("[#6d5a8f]agent is mid-run — /cancel to stop, or wait[/]")
                return
            self._chat(text)

    def _command(self, cmd: str) -> None:
        name, _, arg = cmd.partition(" ")
        name = name.lower()
        if name in ("exit", "quit", "q"):
            self.exit()
        elif name == "help":
            self._help()
        elif name == "clear":
            self.query_one("#chat", RichLog).clear()
            self.query_one("#stream", Static).update("")
            self._stream = ""
        elif name == "demo":
            self._say("[bold #c4b5fd]you[/] › /demo")
            if self._busy:
                self._say("[#6d5a8f]agent busy — /cancel first[/]")
            else:
                self._start_agent(["demo.subject@example.org"], demo=True)
        elif name == "status":
            subj = " · ".join(self._subject) if self._subject else "none"
            self._agent_say(esc(f"model {self.model} · budget {self.budget} calls · "
                                f"busy={self._busy} · subject {subj} · "
                                f"ledger {self._ledger_rows()} rows"))
        elif name == "cancel":
            if self._planner is not None:
                self._planner.stop()
                self._say("[#c4b5fd]cancel requested — finishing current batch[/]")
            else:
                self._say("[#6d5a8f]nothing running[/]")
        else:
            self._say(f"[#6d5a8f]unknown command /{esc(name)} — try /help[/]")

    def _help(self) -> None:
        self._agent_say(esc("talk to me about anything — or hand me a lead to "
                            "audit it: email · username · domain · url · phone "
                            "('audit you@example.com' works too)."))
        self._say("[#6d5a8f]chit-chat is free: 'yo' · 'what can you do' · 'tell me a joke'[/]")
        self._say("[#6d5a8f]/demo offline replay · /clear · /status · /cancel · /help · /exit[/]")

    # ------------------------------------------------------------- chat agent

    def _chat(self, text: str) -> None:
        self.run_worker(self._chat_run(text), exclusive=True)

    async def _chat_run(self, text: str) -> None:
        self._busy = True
        try:
            local = self._local_intent(text)
            if local:
                reply = local
            else:
                reply = await self._llm_reply(text)
                self._history.append({"role": "user", "content": text})
                self._history.append({"role": "assistant", "content": reply})
                del self._history[:-12]        # keep the last ~6 turns
            self._agent_say(esc(reply))
        except Exception:  # noqa: BLE001 — never crash the TUI
            self._agent_say(esc(self._offline_reply()))
        finally:
            self._busy = False
            self._stream = ""
            self.query_one("#stream", Static).update("")
            self.query_one("#input", Input).focus()

    async def _llm_reply(self, text: str) -> str:
        if self._llm is False:
            return self._offline_reply()
        if self._llm is None:
            try:
                from thousand_eyes.agent import client_for
                self._llm = client_for()
            except RuntimeError:
                self._llm = False
                return self._offline_reply()
        out = ""
        try:
            messages = [{"role": "system", "content": CHAT_SYSTEM}] + list(self._history)
            messages.append({"role": "user", "content": text})
            async for chunk, _st in self._llm.chat(messages):
                out += chunk
                self._stream += chunk
                self.query_one("#stream", Static).update(self._stream[-3000:])
        except Exception:  # noqa: BLE001 — chat is best-effort
            return self._offline_reply()
        return out.strip() or self._offline_reply()

    def _offline_reply(self) -> str:
        return ("no Gemini key — chat needs GEMINI_API_KEY (or ~/.gemini/api-key). "
                "meanwhile, hand me a lead (email · username · domain · url · "
                "phone) or run /demo to watch an offline audit.")

    def _local_intent(self, text: str) -> Optional[str]:
        """Operator dashboard words — answered locally, instantly, no LLM."""
        low = text.lower()
        if any(k in low for k in ("finding", "findings", "evidence", "results",
                                  "what did you", "what have you")):
            pl = self._planner
            rows = (pl.ledger if pl is not None else self._last_ledger)[-5:]
            if rows:
                return "\n".join(
                    f"· {r.get('tool')} — {r.get('summary', '')[:80]}" for r in rows)
            return "no evidence rows yet — hand me a lead and i'll start the sweep."
        if any(k in low for k in ("progress", "status")):
            pl = self._planner
            if pl is not None:
                a = pl.analytics.summary()
                return (f"run in flight — {a.get('tool_calls', 0)} tool calls · "
                        f"{a.get('pipelines_fired', 0)} batches · "
                        f"{a.get('leads_seen', 0)} leads · ledger {len(pl.ledger)} rows")
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

    # ------------------------------------------------------------- agent

    def _start_agent(self, leads: List[str], demo: bool = False) -> None:
        self._busy = True
        self._stream = ""
        self._demo = demo
        self._run_start = time.monotonic()
        # honest ETA: demo replay is seconds, live sweep is minutes
        self._bar_total = 45.0 if demo else 180.0
        self.query_one("#stream", Static).update("")
        self._set_status(f"waking agent ({self.model})…")
        self.run_worker(self._agent_run(leads, demo), exclusive=True)

    async def _agent_run(self, leads: List[str], demo: bool) -> None:
        from thousand_eyes.agent import Planner, client_for
        from thousand_eyes.demo import demo_transport

        try:
            llm = client_for()
        except RuntimeError as exc:
            self._agent_say(esc(str(exc)))
            self._busy = False
            return
        self.model = llm.model
        try:
            try:
                planner = Planner(
                    llm, handlers=[self._event], goal="interactive self-audit",
                    budget_calls=self.budget,
                    transport=demo_transport() if demo else None)
            except TypeError:  # older planner signature without transport
                planner = Planner(llm, handlers=[self._event], goal="interactive self-audit",
                                  budget_calls=self.budget)
            self._planner = planner
            self._agent_say(esc(f"auditing {' · '.join(leads)} — fenced, read-only, evidence-only"))
            report = await planner.run(leads)
        except RuntimeError as exc:
            self._hud_idle()
            self._agent_say(f"[red]{esc(str(exc))}[/]")
        except Exception as exc:  # noqa: BLE001 — never crash the TUI
            self._hud_idle()
            self._agent_say(f"[red]{type(exc).__name__}: {esc(str(exc))}[/]")
        else:
            if report:
                self._last_report = report
                self._last_ledger = planner.ledger
                self._say(f"[bold #a78bfa]═══ dossier ═══[/]\n{esc(report)}")
        finally:
            self._planner = None
            self._stream = ""
            self._busy = False
            try:
                await llm.aclose()
            except Exception:  # noqa: BLE001
                pass
            self.query_one("#input", Input).focus()

    async def _event(self, ev) -> None:
        kind, data = ev
        if kind == "llm":
            self._stream += data.get("chunk", "")
            self.query_one("#stream", Static).update(self._stream[-3000:])
        elif kind == "ready":
            self._hud_live()
            self._set_status(f"fence armed · {data.get('tools')} tools · model {self.model}")
        elif kind == "round":
            self._set_status(f"pipeline: depth {data.get('depth')} · "
                             f"{data.get('tools')} tools fired · ledger {data.get('ledger')} rows")
        elif kind == "tool":
            self._set_status(f"tool: {data.get('name')} ok={data.get('ok')} "
                             f"n={data.get('n')} {data.get('ms')}ms")
        elif kind == "audit":
            picked = data.get("picked") or []
            self._set_status(f"self-audit → next families: {', '.join(picked) if picked else '(coverage good)'}")
        elif kind == "budget":
            self._set_status("budget cap hit — stopping")
        elif kind == "done":
            a = data.get("analytics", {})
            self._hud_done(a)
            self._set_status(f"done: {a.get('tool_calls', 0)} tool calls · "
                             f"{a.get('pivots', 0)} pivots · {a.get('llm_calls', 0)} llm calls")


def esc(text: str) -> str:
    """Escape literal '[' so LLM/markup text can't break markup."""
    return text.replace("[", ESC)

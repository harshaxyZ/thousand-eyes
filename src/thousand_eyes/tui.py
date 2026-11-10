"""thousand-eyes interactive TUI — a chat-first agent console.

Bare `thousand-eyes` opens this: black terminal, the banner up top, a
streaming chat, a live token pane, a pipeline status line and a one-line
prompt. It is chat-first: chit-chat answers locally or via the LLM, and an
audit fires ONLY when you hand it a real identifier — email, username,
domain, url, phone — or say an audit word with one. "yo what's up" never
scans anything.

Commands:  /demo  /clear  /status  /cancel  /help  /exit
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

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
_GREET_RE = re.compile(
    r"\b(yo|hey|hi|hello|sup|hiya|howdy|gm|gn|morning|evening|"
    r"what'?s\s*up|wassup|how'?s\s*it\s*going|how\s*are\s*you)\b", re.I)

# conversation words that are handle-shaped but never leads
_LAST_TOKEN_STOP = {
    "data", "stuff", "things", "thing", "info", "information", "details",
    "everything", "nothing", "something", "anything", "that", "this", "out",
    "now", "then", "more", "again", "all", "some", "any", "it", "its", "me",
    "my", "your", "the", "and", "or", "to", "of", "in", "on", "for", "with",
    "about", "into", "over", "under", "once", "here", "there", "is", "are",
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

CHAT_SYSTEM = (
    "You are thousand-eyes, a terse OSINT self-audit agent. Reply in at most "
    "3 short sentences, no markdown, no headers, no emojis, no ANSI codes. "
    "You only ever discuss public, keyless, read-only data. You never "
    "fabricate findings and never claim something is absent — uncertainty is "
    "INCONCLUSIVE. If the operator asks for facts about a person, say you "
    "need a lead (email, username, domain, url or phone) to run an audit."
)


def route_intent(text: str) -> Tuple[str, Optional[str]]:
    """Classify a chat line into (action, payload).

    Actions: audit (payload = the lead) · ask_lead · greet · help · chat.
    An audit fires only on a real identifier, optionally prefixed by an
    audit word — never on plain conversation.
    """
    s = text.strip()
    low = s.lower()
    wants = bool(_AUDIT_RE.search(low))

    cand: Optional[str] = None
    explicit = False          # identifier-shaped, not inferable from a verb
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
    # '<audit verb> <handle>' — last token is the lead (multi-word junk like
    # "search the dark web" must not grab "web")
    if cand is None and wants:
        tok = s.split()[-1].rstrip(".,);:!?")
        if (len(s.split()) <= 3 and _HANDLE_RE.match(tok)
                and tok.lower() not in _STOP_WORDS
                and tok.lower() not in _LAST_TOKEN_STOP):
            cand, explicit = tok, True

    if cand and (explicit or wants):
        return "audit", cand
    if wants:
        return "ask_lead", None
    if any(k in low for k in ("finding", "findings", "evidence", "results",
                              "what did you", "what have you")):
        return "chat", None          # handled by _canned: last 5 ledger rows
    if any(k in low for k in ("progress", "status")):
        return "chat", None          # handled by _canned: pipeline numbers
    if any(k in low for k in ("fence", "safe", "safety", "permission")):
        return "chat", None          # handled by _canned: the fence line
    if _GREET_RE.search(low) and len(s.split()) <= 5:
        return "greet", None
    if any(k in low for k in ("what can you", "how do you", "who are you",
                              "what are you", "help")):
        return "help", None
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


# ------------------------------------------------------------- the app

class ThousandEyesApp(App):
    """The chat console: black terminal, purple accents, agent in the middle."""

    TITLE = "thousand-eyes"
    SUB_TITLE = "see everything. miss nothing."

    CSS = """
    Screen {
        background: #000000;
        color: #d6d6d6;
    }
    Header {
        background: #0a0a0a;
        color: #b46cff;
        text-style: bold;
    }
    Footer {
        background: #0a0a0a;
        color: #6b4d8f;
    }
    #banner {
        border: tall #3a1a5e;
        padding: 0 1;
        background: #000000;
        max_height: 10;
    }
    #chat {
        border: round #3a1a5e;
        padding: 0 1;
        background: #050505;
        color: #d6d6d6;
    }
    #stream {
        border: round #241031;
        padding: 0 1;
        color: #8a63c9;
        background: #000000;
        height: 5;
    }
    #status {
        color: #b46cff;
        background: #0a0a0a;
        padding: 0 1;
        height: 1;
    }
    #input {
        border: tall #3a1a5e;
        background: #0a0a0a;
        color: #f0f0f0;
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
        self._llm: object = None    # cached LLMClient for chit-chat; False = no key
        self._subject: List[str] = []
        self._last_report = ""
        self._last_ledger: List[dict] = []

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._banner_text(), id="banner")
        yield RichLog(id="chat", auto_scroll=True, markup=True)
        yield Static("", id="stream", markup=False)
        yield Static("", id="status")
        yield Input(placeholder="talk to the agent — or type a lead: email · username · domain · url · phone",
                    id="input")
        yield Footer()

    def on_mount(self) -> None:
        self._say("[bold #c77dff]THOUSAND EYES[/] v%s — interactive self-audit" % __version__)
        self._say("[#9d4edd]talk to me — chit-chat is free. audits fire only on real leads.[/]")
        self._say("[#7b2cbf]  email · username · domain · url · phone — or 'audit <lead>'[/]")
        self._say("[#7b2cbf]/demo offline replay · /clear · /status · /cancel · /help · /exit[/]")
        self._say("[#7b2cbf]fence: no onion · no auth probing · no breach dumps — enforced in code[/]")
        self._set_status("fence armed · waiting for a lead")
        self.query_one("#input", Input).focus()

    def _banner_text(self) -> Text:
        try:
            from thousand_eyes.banner import Scene, frame_to_text
            cols = min(self.screen.size.width, 140)
            sc = Scene(max(cols, 60), eye=True, small=True)
            return _parse_ansi_art(frame_to_text(sc.final_frame()))
        except Exception:  # noqa: BLE001 — banner is decoration, never fatal
            return Text("THOUSAND EYES", style="bold #c77dff")

    # ------------------------------------------------------------- output

    def _say(self, text: str) -> None:
        self.query_one("#chat", RichLog).write(text)

    def _agent_say(self, text: str) -> None:
        self._say(f"[bold #c77dff]agent[/] › {text}")

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    # ------------------------------------------------------------- input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._command(text[1:].strip())
            return
        self._say(f"[bold #e0aaff]you[/] › {esc(text)}")
        action, payload = route_intent(text)
        if action == "audit":
            if self._busy:
                self._say("[#7b2cbf]agent is mid-run — /cancel to stop, or wait[/]")
                return
            self._subject = [payload]
            self._start_agent([payload])
        elif action == "ask_lead":
            self._agent_say(esc("hand me the lead to audit — email · username · "
                                "domain · url · phone (e.g. 'audit you@example.com')"))
        elif action == "help":
            self._help()
        elif action in ("greet", "chat"):
            if self._busy:
                self._say("[#7b2cbf]agent is mid-run — /cancel to stop, or wait[/]")
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
            self._say("[bold #e0aaff]you[/] › /demo")
            if self._busy:
                self._say("[#7b2cbf]agent busy — /cancel first[/]")
            else:
                self._start_agent(["demo.subject@example.org"], demo=True)
        elif name == "status":
            subj = " · ".join(self._subject) if self._subject else "none"
            rows = (len(self._planner.ledger) if self._planner is not None
                    else len(self._last_ledger))
            self._agent_say(esc(f"model {self.model} · budget {self.budget} calls · "
                                f"busy={self._busy} · subject {subj} · ledger {rows} rows"))
        elif name == "cancel":
            if self._planner is not None:
                self._planner.stop()
                self._say("[#e0aaff]cancel requested — finishing current batch[/]")
            else:
                self._say("[#7b2cbf]nothing running[/]")
        else:
            self._say(f"[#7b2cbf]unknown command /{esc(name)} — try /help[/]")

    def _help(self) -> None:
        self._agent_say(esc("talk to me — type a lead to audit it: email · username · "
                            "domain · url · phone ('janedoe_' or 'audit you@example.com')."))
        self._say("[#7b2cbf]chit-chat works too: 'yo' · 'what can you do' · 'status' · "
                  "'findings' · 'fence'[/]")
        self._say("[#7b2cbf]/demo offline replay · /clear · /status · /cancel · /help · /exit[/]")

    # ------------------------------------------------------------- chat agent

    def _chat(self, text: str) -> None:
        self.run_worker(self._chat_run(text), exclusive=True)

    async def _chat_run(self, text: str) -> None:
        self._busy = True
        try:
            reply = await self._llm_reply(text)
            self._agent_say(esc(reply))
        except Exception:  # noqa: BLE001 — never crash the TUI
            self._agent_say(esc(self._default_reply()))
        finally:
            self._busy = False
            self._stream = ""
            self.query_one("#stream", Static).update("")
            self.query_one("#input", Input).focus()

    async def _llm_reply(self, text: str) -> str:
        canned = self._canned(text)
        if canned:
            return canned
        if self._llm is False:
            return self._default_reply()
        if self._llm is None:
            try:
                from thousand_eyes.agent import client_for
                self._llm = client_for()
            except RuntimeError:
                self._llm = False
        if not self._llm:
            return self._default_reply()
        out = ""
        try:
            async for chunk, _st in self._llm.chat([
                    {"role": "system", "content": CHAT_SYSTEM},
                    {"role": "user", "content": text}]):
                out += chunk
                self._stream += chunk
                self.query_one("#stream", Static).update(self._stream[-3000:])
        except Exception:  # noqa: BLE001 — chat is best-effort
            return self._default_reply()
        return out.strip() or self._default_reply()

    def _canned(self, text: str) -> Optional[str]:
        """Free local intents — no LLM, no network."""
        low = text.lower()
        if _GREET_RE.search(low):
            return ("yo. thousand-eyes here — hand me a lead (email · username · "
                    "domain · url · phone) or say 'audit <lead>' and i'll sweep it, "
                    "fenced and read-only.")
        if any(k in low for k in ("who are you", "what are you")):
            return ("i'm the thousand-eyes agent: i plan keyless, read-only OSINT "
                    "sweeps over public data, execute them behind a hard fence, and "
                    "report evidence or INCONCLUSIVE — never guesses.")
        if any(k in low for k in ("what can you", "how do you", "help")):
            return ("give me a lead — email, username, domain, url, phone — or say "
                    "'audit <lead>'. /demo replays a synthetic audit offline; "
                    "/status shows the pipeline; /cancel stops a run.")
        if "thank" in low:
            return "np. next lead whenever you want it."
        if "joke" in low:
            return ("why did the osint analyst break up with the honeypot? "
                    "too many indicators of compromise.")
        if any(k in low for k in ("bye", "goodbye", "later", "cya")):
            return "later. /exit closes the booth — the fence stays armed."
        if any(k in low for k in ("finding", "findings", "evidence", "results",
                                  "what did you", "what have you")):
            pl = self._planner
            rows = (pl.ledger if pl is not None else self._last_ledger)[-5:]
            if rows:
                return "\n".join(
                    f"· {r.get('tool')} — {r.get('summary', '')[:80]}"
                    for r in rows)
            return "no evidence rows yet — hand me a lead and i'll start the sweep."
        if any(k in low for k in ("progress", "status", "what's going on", "whats going on")):
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

    def _default_reply(self) -> str:
        return ("i live for the sweep — hand me a lead (email · username · domain · "
                "url · phone) or say 'audit <lead>'. chit-chat is fine but my day "
                "job is evidence.")

    # ------------------------------------------------------------- agent

    def _start_agent(self, leads: List[str], demo: bool = False) -> None:
        self._busy = True
        self._stream = ""
        self.query_one("#stream", Static).update("")
        self._set_status(f"waking agent ({self.model})…")
        self.run_worker(self._agent_run(leads, demo), exclusive=True)

    async def _agent_run(self, leads: List[str], demo: bool) -> None:
        from thousand_eyes.agent import Planner, client_for
        from thousand_eyes.demo import demo_transport

        llm = None
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
            self._agent_say(f"[red]{esc(str(exc))}[/]")
        except Exception as exc:  # noqa: BLE001 — never crash the TUI
            self._agent_say(f"[red]{type(exc).__name__}: {esc(str(exc))}[/]")
        else:
            if report:
                self._last_report = report
                self._last_ledger = planner.ledger
                self._say(f"[bold #c77dff]═══ dossier ═══[/]\n{esc(report)}")
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
            self.query_one("#stream", Static).update(self._stream[-4000:])
        elif kind == "round":
            self._set_status(f"pipeline: depth {data.get('depth')} · "
                             f"{data.get('tools')} tools fired · ledger {data.get('ledger')} rows")
        elif kind == "tool":
            self._set_status(f"tool: {data.get('name')} ok={data.get('ok')} "
                             f"n={data.get('n')} {data.get('ms')}ms")
        elif kind == "audit":
            picked = data.get("picked") or []
            self._set_status(f"self-audit → next families: {', '.join(picked) if picked else '(coverage good)'}")
        elif kind == "ready":
            self._set_status(f"fence armed · {data.get('tools')} tools · model {self.model}")
        elif kind == "budget":
            self._set_status("budget cap hit — stopping")
        elif kind == "done":
            a = data.get("analytics", {})
            self._set_status(f"done: {a.get('tool_calls', 0)} tool calls · "
                             f"{a.get('pivots', 0)} pivots · {a.get('llm_calls', 0)} llm calls")


def esc(text: str) -> str:
    """Escape literal '[' so LLM/markup text can't break markup."""
    return text.replace("[", ESC)

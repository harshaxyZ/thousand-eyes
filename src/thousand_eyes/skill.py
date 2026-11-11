"""Install helpers for the thousand-eyes skill (Claude Code / Codex / any IDE).

Two surfaces, one command each:

  thousand-eyes skill install
      writes .claude/skills/thousand-eyes/SKILL.md + scripts/tui_chat.py into
      the CURRENT project so Claude Code / Codex / Cursor can drive the audit
      TUI themselves (two commands: install CLI, install skill).

  thousand-eyes skill uninstall
      removes exactly what install wrote, then reports what remains.

The skill itself is tiny on purpose: the TUI is the interface. The agent
types a lead, watches the step stream, reads the summary. No other contract.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Tuple

SKILL_DIR = Path(".claude/skills/thousand-eyes")

# The skill card: frontmatter is what Claude Code's skill loader reads.
SKILL_MD = """\
---
name: thousand-eyes
description: OSINT self-audit — one lead in (email · username · domain · url · phone), an impact-ranked evidence dossier out. Drives the thousand-eyes TUI; read-only, fenced, evidence-only.
---

# thousand-eyes

Public-data self-audit. The operator types a lead; the TUI sweeps every
confirmed pivot outwards and ranks what an attacker could do with it.

## Two commands

1. install the CLI:    pipx install thousand-eyes
2. install this skill: thousand-eyes skill install

## Drive the TUI

    python scripts/tui_chat.py "<lead>"            # one lead, one dossier
    python scripts/tui_chat.py "demo.subject@example.org" --demo

The script boots the real TUI in a pty, types the lead exactly as the
operator would, waits for the run, then prints the SUMMARY block (wall
time, LLM calls, tokens, ttft, tools, breaches, pivots).

## Fence (never cross it)

no .onion resolution · no auth probing · no form submission · no password
tests · no breach dumps · per-domain 300ms bucket · global concurrency 8 ·
planner executes, model selects. Blocked == inconclusive, never faked.

## Read-only shell-out

Inside the TUI, prefix any command with `!` — `!git status`,
`!pip show thousand-eyes`. Output lands in the chat pane. Nothing else runs.
"""

TUI_CHAT_PY = '''\
"""Boot the real thousand-eyes TUI in a pty, type one lead, print the SUMMARY.

Usage:  python scripts/tui_chat.py "<lead>" [--demo] [--timeout S]

Why a pty: the TUI is a Textual app — it renders to a terminal, not to a
pipe. A pty gives it a real terminal (size, keys, redraw) while we read the
scrollback for the [ SUMMARY ] block the run ends with.
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import sys
import time

SUMMARY_RE = re.compile(r"SUMMARY")


def _drain(fd: int, buf: bytearray, seconds: float) -> int:
    """Read whatever the app wrote within `seconds`; returns bytes read."""
    end = time.monotonic() + seconds
    got = 0
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], min(0.1, end - time.monotonic()))
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        got += len(chunk)
    return got


def run(lead: str, demo: bool, timeout: float) -> Tuple[int, str]:
    """Boot the TUI, type the lead, return (rc, scrollback)."""
    argv = [sys.executable, "-m", "thousand_eyes.tui"]
    if demo:
        lead = "/demo"
    pid, fd = pty.fork()
    if pid == 0:                                   # child: the TUI itself
        os.environ.setdefault("LINES", "40")
        os.environ.setdefault("COLUMNS", "100")
        os.environ["TERM"] = "xterm-256color"
        os.execvp(argv[0], argv)

    buf = bytearray()
    deadline = time.monotonic() + timeout
    typed = False
    rc = 0
    try:
        while time.monotonic() < deadline:
            got = _drain(fd, buf, 0.25)
            if not typed and got:
                # let the banner settle for one beat, then type the lead
                time.sleep(0.5)
                os.write(fd, (lead + "\\r").encode())
                typed = True
            if SUMMARY_RE.search(buf[-4000:]):
                break
            if not got and typed:
                break
    finally:
        try:
            os.write(fd, b"\\x03")                  # ctrl+c: stop the run
            time.sleep(0.3)
            os.write(fd, b"\\x03")                  # and quit the app
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            _, status = os.waitpid(pid, 0)
            rc = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            rc = 0
    return rc, buf.decode("utf-8", "replace")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lead", help="email · username · domain · url · phone")
    ap.add_argument("--demo", action="store_true", help="offline replay")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="seconds to wait for the run (default 120)")
    args = ap.parse_args(argv)
    rc, text = run(args.lead, args.demo, args.timeout)
    # strip ANSI, keep the summary onward
    clean = re.sub(r"\\x1b\\[[0-9;?]*[a-zA-Z]", "", text)
    i = clean.find("[ SUMMARY ]")
    print(clean[i:] if i >= 0 else clean[-4000:])
    return rc


if __name__ == "__main__":
    sys.exit(main())
'''


def _root() -> Path:
    """Project root: this file lives in src/thousand_eyes/."""
    return Path(__file__).resolve().parent.parent.parent


def install() -> Tuple[int, List[str]]:
    """Write the skill into .claude/skills/thousand-eyes. Returns (rc, lines)."""
    root = _root()
    target = root / SKILL_DIR
    scripts = target / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (scripts / "tui_chat.py").write_text(TUI_CHAT_PY, encoding="utf-8")
    lines = [f"wrote {target / 'SKILL.md'}",
             f"wrote {scripts / 'tui_chat.py'}"]
    return 0, lines


def uninstall() -> Tuple[int, List[str]]:
    """Remove exactly what install wrote. Returns (rc, lines)."""
    target = _root() / SKILL_DIR
    if not target.exists():
        return 0, ["skill not present — nothing to remove"]
    shutil.rmtree(target)
    return 0, [f"removed {target}"]


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "install":
        rc, lines = install()
    elif argv and argv[0] == "uninstall":
        rc, lines = uninstall()
    else:
        print(__doc__)
        return 0
    for ln in lines:
        print(ln)
    return rc

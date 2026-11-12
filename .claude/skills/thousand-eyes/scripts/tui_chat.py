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
                os.write(fd, (lead + "\r").encode())
                typed = True
            if SUMMARY_RE.search(buf[-4000:]):
                break
            if not got and typed:
                break
    finally:
        try:
            os.write(fd, b"\x03")                  # ctrl+c: stop the run
            time.sleep(0.3)
            os.write(fd, b"\x03")                  # and quit the app
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
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    i = clean.find("[ SUMMARY ]")
    print(clean[i:] if i >= 0 else clean[-4000:])
    return rc


if __name__ == "__main__":
    sys.exit(main())

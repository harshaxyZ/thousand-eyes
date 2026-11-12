"""`thousand-eyes --doctor`: one command that tells you if the tool is
actually ready to work on this machine.

Checks the python/runtime, the install location, the optional dependencies,
the LLM key and the IDE skill, then probes a handful of keyless endpoints
for reachability and latency. Everything is read-only and free.
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import time

import httpx

PROBES = [
    ("github api", "https://api.github.com/users/janedoe"),
    ("hacker news", "https://hn.algolia.com/api/v1/users/pg"),
    ("gravatar", "https://gravatar.com/avatar/00000000000000000000000000000000"),
    ("chess.com", "https://api.chess.com/pub/player/hikaru"),
    ("wayback", "https://web.archive.org/cdx/search/cdx?url=example.com&limit=1"),
]


def _line(ok: bool, label: str, detail: str = "") -> str:
    mark = "ok  " if ok else "warn"
    return f"  [{mark}] {label}" + (f" - {detail}" if detail else "")


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> tuple[bool, str]:
    t0 = time.monotonic()
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}"
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code >= 500:
        return False, f"status {resp.status_code} ({ms}ms)"
    return True, f"status {resp.status_code} ({ms}ms)"


def run_doctor() -> int:
    print("thousand-eyes doctor")
    print("")
    print("runtime")
    print(_line(sys.version_info[:2] >= (3, 10), "python",
                f"{platform.python_version()} at {sys.executable}"))
    print(_line(True, "platform", f"{platform.system()} {platform.release()}"))

    print("")
    print("install")
    try:
        from thousand_eyes import __version__
        print(_line(True, "package", f"thousand-eyes {__version__}"))
    except Exception as exc:                       # pragma: no cover
        print(_line(False, "package", str(exc)))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(_line(True, "environment",
                "venv" if in_venv else "system python"))
    try:
        import numpy  # noqa: F401
        print(_line(True, "numpy", f"{numpy.__version__} (banner only)"))
    except ImportError:
        print(_line(False, "numpy", "not installed - the banner falls back to text "
                                   "(pipx inject thousand-eyes numpy)"))

    print("")
    print("intelligence")
    try:
        from thousand_eyes.setup import status_line
        print(_line(True, "llm key", status_line().replace("llm key: ", "")))
    except Exception as exc:
        print(_line(False, "llm key", str(exc)))
    try:
        from thousand_eyes.skill import skill_status
        print(_line(True, "ide skill", skill_status().replace("skill: ", "")))
    except Exception as exc:
        print(_line(False, "ide skill", str(exc)))

    print("")
    print("network (free, keyless, read-only probes)")
    probes: list[tuple[bool, str]] = []

    async def _go() -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0),
                                     headers={"User-Agent": "thousand-eyes-doctor"}) as c:
            for name, url in PROBES:
                probes.append(await _probe(c, name, url))

    try:
        asyncio.run(_go())
    except Exception as exc:                       # pragma: no cover
        print(_line(False, "probes", str(exc)))
        probes = []

    for (name, _url), (ok, detail) in zip(PROBES, probes):
        print(_line(ok, name, detail))

    reachable = sum(1 for ok, _ in probes if ok)
    print("")
    if reachable == len(PROBES) and reachable:
        print(f"  all {reachable} probe endpoints answered - ready.")
        return 0
    print(f"  {reachable}/{len(PROBES)} probe endpoints answered; the rest are "
          "transient or blocked. findings stay INCONCLUSIVE, never faked.")
    return 0

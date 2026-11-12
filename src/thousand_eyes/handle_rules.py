"""Handle extraction: turn a confirmed profile URL into the handle it proves.

The cascade needs glue between nodes. A profile URL that a real response
confirmed (github.com/janedoe came back from a gravatar profile, a
commit search, a username sweep) proves that the HANDLE exists on that
platform - so the handle can become the next lead.

This is NOT guessing: the handle comes from a response-confirmed profile
URL, never from the email local-part and never from a guess (hard rule #2).

Two sources of truth:
  * the bundled WhatsMyName dataset (692 profile URL templates), used to
    recognise a profile URL shape and pull the handle out of it;
  * a small table of platforms whose profile URLs the dataset does not
    cover (bluesky, x/twitter, chess, lichess, codeberg, dev.to, docker
    hub, keybase, hacker news).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from urllib.parse import urlparse

_HANDLE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# platform -> how to pull the handle out of the path/query
_EXTRA_RULES: list[tuple[str, str]] = [
    ("bsky.app", "path2"),            # /profile/<handle>
    ("x.com", "path1"),
    ("twitter.com", "path1"),
    ("news.ycombinator.com", "query"),
    ("www.chess.com", "member"),
    ("chess.com", "member"),
    ("lichess.org", "at"),
    ("codeberg.org", "path1"),
    ("dev.to", "path1"),
    ("hub.docker.com", "u"),
    ("keybase.io", "path1"),
    ("gitlab.com", "path1"),
    ("github.com", "path1"),
]


@lru_cache(maxsize=1)
def _templates() -> list[tuple[str, str]]:
    """(prefix, suffix) pairs from the WMN dataset templates."""
    try:
        text = resources.files("thousand_eyes.data").joinpath(
            "wmn_data.json").read_text(encoding="utf-8")
        sites = (json.loads(text) or {}).get("sites", [])
    except Exception:      # noqa: BLE001 - dataset missing: extras still work
        return []
    out: list[tuple[str, str]] = []
    for site in sites:
        uri = site.get("uri_check") or ""
        if "{account}" not in uri:
            continue
        prefix, suffix = uri.split("{account}", 1)
        out.append((prefix, suffix))
    # longest prefix first: the most specific template wins
    out.sort(key=lambda p: len(p[0]), reverse=True)
    return out


def _clean(handle: str) -> str:
    handle = handle.strip().strip("/").lstrip("@")
    if handle.endswith(".bsky.social"):
        handle = handle[: -len(".bsky.social")]
    if not handle or not _HANDLE_OK.match(handle):
        return ""
    return handle


def _from_extra(url: str, host: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    for domain, mode in _EXTRA_RULES:
        if host != domain and not host.endswith("." + domain):
            continue
        if mode == "path1":
            return _clean(parts[0]) if parts else ""
        if mode == "path2":
            return _clean(parts[1]) if len(parts) > 1 else ""
        if mode == "member":
            if len(parts) >= 2 and parts[0] == "member":
                return _clean(parts[1])
            return ""
        if mode == "at":
            if len(parts) >= 2 and parts[0] == "@":
                return _clean(parts[1])
            if parts and parts[0].startswith("@"):
                return _clean(parts[0])
            return ""
        if mode == "u":
            if len(parts) >= 2 and parts[0] == "u":
                return _clean(parts[1])
            return ""
        if mode == "query":
            qs = parsed.query
            for part in qs.split("&"):
                if part.startswith("id="):
                    return _clean(part[3:])
            return ""
    return ""


def extract_handle(url: str) -> str:
    """The handle this profile URL proves, or "" when the URL is not a
    profile page we can recognise."""
    if not url or "://" not in url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return ""

    # dataset templates first (they know 692 profile shapes)
    for prefix, suffix in _templates():
        if not url.startswith(prefix):
            continue
        if suffix and not url.endswith(suffix):
            continue
        middle = url[len(prefix): len(url) - len(suffix)] if suffix else url[len(prefix):]
        handle = _clean(middle)
        if handle:
            return handle

    return _from_extra(url, host)


def known_profile(url: str) -> bool:
    return bool(extract_handle(url))

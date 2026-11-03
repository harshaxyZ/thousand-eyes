"""username-sweep module (v0.3 wildfire core).

Reads the bundled WhatsMyName dataset (CC-BY-SA 4.0 — see
data/ATTRIBUTION-WhatsMyName.md) and runs OUR OWN single-GET interpretation
of each site's check rule — none of the WMN tool code is imported.

Verdict per site, mirroring the tool's own marker philosophy (same safety
net as our registration leaves):
  claimed    = response matches the e_code AND no contradicting e_string
  unclaimed  = f_code/f_string matched (or the marker philosophy says absent)
  ambiguous  = anything else ("both markers", "neither") → dropped silently,
               never reported as a finding — a stale or wrong dataset entry
               can therefore never manufacture a false positive.

A confirmed profile URL becomes a url pivot (type "url"), which the caller
may enqueue for a fetch at the next depth — that is the wildfire spread.

--wild (all sites) vs default: dataset sites are ordered but capped at
DEFAULT_MAX_SITES per username for runtime politeness; the order is stable
(seeded shuffle by username) so runs are reproducible.
"""

from __future__ import annotations

import json
import random
import re
from importlib import resources

import httpx

from thousand_eyes.http_client import describe_http_error
from thousand_eyes.extract import visible_text

MODULE = "username-sweep"

DEFAULT_MAX_SITES = 120
CONCURRENCY = 6
STAGGER_SECONDS = 0.25
# WMN v4 schema (verified against wmn-data.json + scripts/checker/wmn_check.py
# on 2026-11-03): uri templates use {account} (older forks: {u}/{username});
# e_code/e_string = EXISTS markers (e_string must be PRESENT in the body),
# m_code/m_string (+legacy f_code/f_string) = MISSING markers.
_PLACEHOLDER_RE = re.compile(r"\{(account|u|username|user)\}")
_PLACEHOLDER_TOKEN = "{account}"


def _load_sites() -> list[dict]:
    text = resources.files("thousand_eyes.data").joinpath("wmn_data.json").read_text(encoding="utf-8")
    return (json.loads(text) or {}).get("sites", [])


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [s for s in v if isinstance(s, str)]


def _hit_strings(site: dict) -> tuple[list[str], list[str]]:
    """All EXISTS markers / all MISSING markers, normalised to lists."""
    return _as_list(site.get("e_string")), _as_list(site.get("m_string")) + _as_list(site.get("f_string"))


def _marker_codes(site: dict) -> tuple[int | None, int | None]:
    return site.get("e_code"), site.get("m_code") or site.get("f_code")


def normalize_placeholder(uri: str) -> str | None:
    """WMN uris use {account} (v4) or {u}/{username} (older forks); map them
    all to one str.format-able token. Sites with no placeholder are skipped."""
    if not uri:
        return None
    if _PLACEHOLDER_RE.search(uri):
        return _PLACEHOLDER_RE.sub(_PLACEHOLDER_TOKEN, uri)
    if "{" not in uri and "}" not in uri:
        return uri  # no placeholder at all — not usable for a username probe
    return None


async def _check_site(client: httpx.AsyncClient, site: dict, username: str) -> tuple[str, dict | None]:
    """Returns ("claimed", pivot-dict) | ("unclaimed", None) | ("ambiguous", None) | ("error", None).

    v4 verdict logic (mirrors WMN's own checker):
      claimed   = status == e_code AND (no e_strings OR at least one e_string in body)
      unclaimed = status == m-code AND (no m_strings OR at least one m_string in body)
      ambiguous = anything else -> dropped silently, never a false positive
    """
    uri = normalize_placeholder(site.get("uri_check", ""))
    if not uri or _PLACEHOLDER_TOKEN not in uri:
        return "ambiguous", None
    url = uri.replace(_PLACEHOLDER_TOKEN, username)

    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        _ = describe_http_error(exc)  # transport-level failure: inconclusive, not a negative
        return "error", None

    e_code, m_code = _marker_codes(site)
    e_strings, m_strings = _hit_strings(site)

    body = visible_text(resp.text).lower()
    status = resp.status_code

    if e_code is not None and status == e_code:
        if not e_strings or any(marker.lower() in body for marker in e_strings):
            # WMN stores uri_pretty = the HUMAN profile URL (the checker uses
            # it as the "Profile ↗" link). Claim shows the profile page; the
            # API check URL that proved existence stays as the evidence.
            pretty = str(site.get("uri_pretty") or "").strip()
            if pretty and "{" in pretty and "}" in pretty:
                pretty = _PLACEHOLDER_RE.sub(_PLACEHOLDER_TOKEN, pretty).replace(_PLACEHOLDER_TOKEN, username)
            return "claimed", {
                "name": site.get("name", "unknown"),
                "category": site.get("cat") or site.get("category"),
                "url": pretty or url,
                "status": status,
                "uri_check": url,
            }

    if m_code is not None and status == m_code:
        if not m_strings or any(marker.lower() in body for marker in m_strings):
            return "unclaimed", None

    return "ambiguous", None


async def run(client: httpx.AsyncClient, username: str, *, max_sites: int = DEFAULT_MAX_SITES, all_sites: bool = False) -> dict:
    """Returns {"pivots": [...], "errors": [...], "checked": int}."""
    sites = _load_sites()
    # stable shuffle keyed by username so sweep order differs per user but is reproducible
    rng = random.Random(username)
    shuffled = sites[:]
    rng.shuffle(shuffled)
    if not all_sites:
        shuffled = shuffled[:max_sites]

    import asyncio
    sem = asyncio.Semaphore(CONCURRENCY)

    pivots: list[dict] = []
    errors: list[dict] = []
    ambiguous = 0
    checked = 0

    async def one(site: dict, index: int) -> None:
        nonlocal ambiguous, checked
        async with sem:
            await asyncio.sleep((index % 8) * STAGGER_SECONDS)  # cheap global pacing
            verdict, payload = await _check_site(client, site, username)
            if verdict != "error":
                checked += 1
            if verdict == "claimed" and payload:
                pivots.append({
                    "type": "url",
                    "value": payload["url"],
                    "source": f"{MODULE}:{payload['name']}",
                    "evidence_url": payload["url"],
                    "snippet": (
                        f"{payload['name']} ({payload['category'] or 'uncategorized'}): "
                        f"profile responds {payload['status']} with the claimed-user marker pattern"
                    ),
                    "status": payload["status"],
                    "site": payload["name"],
                })
            elif verdict == "error":
                errors.append({"module": f"{MODULE}:{site.get('name', '?')}", "error": "request failed (timeout / connection)"})
            elif verdict == "ambiguous":
                ambiguous += 1

    await asyncio.gather(*(one(s, i) for i, s in enumerate(shuffled)))

    pivots.sort(key=lambda p: p["value"])
    return {"pivots": pivots, "errors": errors, "ambiguous": ambiguous, "checked": checked}

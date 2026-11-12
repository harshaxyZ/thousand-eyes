"""breach-existence - the keyless "is my email in public leaks" layer.

Primary (keyless, verified live 2026-11-03):
  GET https://api.xposedornot.com/v1/check-email/<email>
  → clean:    {"Error":"Not found","email":null}
  → exposed:  {"breaches_details":{...}, ...}  (breach NAMES only - this is
    existence-attribution, never dump contents)

Optional (key): Hudson Rock stealer-log check - free API key from
hudsonrock.com; the endpoint shape is only shipable when HUDSON_API_KEY
is set, so it degrades to skip rather than guess a URL.
"""

from __future__ import annotations

import os

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "breach-existence"
XON_URL = "https://api.xposedornot.com/v1/check-email/{email}"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "leaves": [...], "error": str|None}.

    A confirmed breach hit is a LEAF (site=breach-name, evidence=url) - it
    confirms existence, it is not a new identifier to expand.
    """
    url = XON_URL.format(email=email)
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code != 200:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: invalid json response ({exc})"}

    leaves: list[dict] = []
    details = (data.get("breaches_details") or {})
    breach_map = details.get("breaches") or {}

    if isinstance(breach_map, dict) and breach_map:
        for breach_name, meta in breach_map.items():
            site_label = breach_name
            if isinstance(meta, dict) and meta.get("breach_date"):
                site_label = f"{breach_name} ({meta['breach_date']})"
            leaves.append({"site": site_label, "url": url, "status": resp.status_code, "category": "breach"})

    return {"pivots": [], "leaves": leaves, "error": None}


async def run_hudsonrock(client: httpx.AsyncClient, email: str) -> dict:
    """Optional keyed stealer-log existence check. Skips without a key; an
    invalid key must surface as an error, never as a clean bill (hard rule #7)."""
    key = os.environ.get("HUDSON_API_KEY")
    if not key:
        # NOTE: no MODULE prefix here - _record adds the module label itself
        return {"pivots": [], "leaves": [], "error": None, "skipped": "no HUDSON_API_KEY set (stealer-log check stays a manual browser step)"}
    url = "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email"
    try:
        resp = await client.get(url, params={"email": email}, headers={"apikey": key})
    except httpx.HTTPError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: {describe_http_error(exc)}"}
    if resp.status_code != 200:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: unexpected status {resp.status_code}"}
    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: invalid json ({exc})"}
    leaves: list[dict] = []
    tools = (data.get("stealfinder") or [])
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            leaves.append({"site": f"stealer log: {t.get('machine_name') or 'unknown host'}", "url": f"{url}?email={email}", "status": resp.status_code, "category": "stealer"})
    return {"pivots": [], "leaves": leaves, "error": None}

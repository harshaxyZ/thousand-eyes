"""leakcheck - keyless breach-source existence for an email.

GET https://leakcheck.io/api/public?check=<email>   (verified live 2026-11-13)
  -> found:     {"success": true, "found": N, "fields": [...],
                 "sources": [{"name": "Site.com", "date": "2020-06"}, ...]}
  -> not found: {"success": false, "error": "Not found"}

The public endpoint returns only WHICH breaches list the address and WHICH
kinds of fields those records hold (e.g. "password", "phone"). It never
returns the records themselves, so this stays an existence check (hard rule
#3: no breach-dump pulls). Each source is a LEAF, never a pivot.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "leakcheck"
API_URL = "https://leakcheck.io/api/public"
MAX_SOURCES = 40      # listed breach sources per email (newest first)


def _empty(error: str | None = None) -> dict:
    return {"pivots": [], "leaves": [], "error": error}


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [], "leaves": [...], "error": str|None}."""
    try:
        resp = await client.get(API_URL, params={"check": email})
    except httpx.HTTPError as exc:
        return _empty(f"{MODULE}: {describe_http_error(exc)}")
    if resp.status_code == 429:
        return _empty(f"{MODULE}: rate-limited (status 429)")
    if resp.status_code != 200:
        return _empty(f"{MODULE}: unexpected status {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        return _empty(f"{MODULE}: invalid json response ({exc})")
    if not isinstance(data, dict):
        return _empty(f"{MODULE}: unexpected response shape")

    if not data.get("success"):
        reason = str(data.get("error") or "")
        if reason.strip().lower() == "not found":
            return _empty()                       # a real, clean answer
        return _empty(f"{MODULE}: {reason or 'request refused'}")

    evidence = f"{API_URL}?check={email}"
    sources = [s for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("name")]
    sources.sort(key=lambda s: str(s.get("date") or ""), reverse=True)

    leaves: list[dict] = []
    for src in sources[:MAX_SOURCES]:
        label = f"breach: {src['name']}"
        if src.get("date"):
            label += f" ({src['date']})"
        leaves.append({"site": label, "url": evidence, "status": resp.status_code,
                       "category": "breach"})

    fields = [str(f) for f in (data.get("fields") or []) if f]
    total = data.get("found") or len(sources)
    if total:
        summary = f"breach summary: {total} leaked record(s) in {len(sources)} source(s)"
        if fields:
            summary += " - exposed field types: " + ", ".join(sorted(fields)[:20])
        if len(sources) > MAX_SOURCES:
            summary += f" (showing the {MAX_SOURCES} newest sources)"
        leaves.insert(0, {"site": summary, "url": evidence,
                          "status": resp.status_code, "category": "breach"})
    return {"pivots": [], "leaves": leaves, "error": None}

"""reddit-user — public user about.json (keyless; verified shape 2026-11-03).

GET https://www.reddit.com/user/<username>/about.json
→ 200 {"data": {"name", "created_utc", "total_karma", "link_karma", ...}}
→ 403 = anti-bot tightening (an ERROR, never collapsed into silence)
→ 404 = no such user (silence)

Late-2026 reality: Reddit gates anonymous .json aggressively from some
networks/UAs. We send a normal descriptive UA and accept that 403 may be
the common outcome — reported, not hidden.
"""

from __future__ import annotations

import datetime as dt

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "reddit-user"
API_URL = "https://www.reddit.com/user/{username}/about.json"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = API_URL.format(username=username)
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code == 404:
        return {"pivots": [], "error": None}
    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: reddit blocked the anonymous .json read (status {resp.status_code})"}
    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    payload = data.get("data") or {}
    name = payload.get("name")
    if not name:
        return {"pivots": [], "error": None}

    created = payload.get("created_utc")
    snippet = f"reddit user /u/{name}"
    if isinstance(created, (int, float)):
        age = dt.datetime.fromtimestamp(created, tz=dt.timezone.utc).strftime("%Y-%m-%d")
        snippet += f", created {age}"
    karma = payload.get("total_karma")
    if isinstance(karma, (int, float)):
        snippet += f", total karma {int(karma)}"

    return {
        "pivots": [{
            "type": "url",
            "value": f"https://www.reddit.com/user/{name}/",
            "source": MODULE,
            "evidence_url": url,
            "snippet": snippet,
            "status": resp.status_code,
        }],
        "error": None,
    }

"""Module: wikipedia-user.

Keyless Wikipedia account lookup via the public API:
GET https://en.wikipedia.org/w/api.php?action=query&list=users&ususers={u}

The API answers 200 for unknown users too, but marks them with a "missing"
field - only a row WITHOUT "missing" (it carries a userid) CONFIRMS the
account (hard rule #1). A missing account is silence, not an error.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "wikipedia-user"
API_URL = "https://en.wikipedia.org/w/api.php"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    params = {"action": "query", "list": "users", "ususers": username, "format": "json"}
    try:
        resp = await client.get(API_URL, params=params)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}
    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    users = ((data.get("query") or {}).get("users") or [])
    row = next((u for u in users
                if isinstance(u, dict) and not u.get("missing")), None)
    if row is None:
        return {"pivots": [], "error": None}   # account does not exist

    name = (row.get("name") or "").strip()
    profile_url = f"https://en.wikipedia.org/wiki/User:{name or username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": str(resp.url),
        "snippet": f"Wikipedia: userid={row.get('userid')} name=\"{name}\"",
        "status": resp.status_code,
    }]

    if name and name.lower() != username.lower():
        pivots.append({
            "type": "name",
            "value": name,
            "source": MODULE,
            "evidence_url": str(resp.url),
            "snippet": f"Wikipedia account name=\"{name}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

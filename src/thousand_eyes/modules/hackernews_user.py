"""Module: hackernews-user.

Keyless Hacker News user lookup via the public Algolia API:
GET https://hn.algolia.com/api/v1/users/{u}

A 200 body whose "username" matches exactly CONFIRMS the account (hard
rule #1). 404 means absent (silence, not an error). Karma and about text
are literal API facts.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "hackernews-user"
API_URL = "https://hn.algolia.com/api/v1/users"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = f"{API_URL}/{username}"
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}
    if resp.status_code == 404:
        return {"pivots": [], "error": None}
    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    if (data.get("username") or "").strip() != username:
        return {"pivots": [], "error": None}

    profile_url = f"https://news.ycombinator.com/user?id={username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"HN Algolia: username=\"{username}\"",
        "status": resp.status_code,
    }]

    karma = data.get("karma")
    if isinstance(karma, int):
        pivots.append({
            "type": "fact",
            "value": f"hacker news account karma {karma}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"HN karma={karma} (established account when > 0)",
            "status": resp.status_code,
        })

    about = (data.get("about") or "").strip()
    if about:
        pivots.append({
            "type": "fact",
            "value": f"hacker news bio: {about[:120]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": "HN about field (operator-written bio)",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

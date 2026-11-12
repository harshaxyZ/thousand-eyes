"""Module: chess-profile.

Keyless Chess.com public player lookup:
GET https://api.chess.com/pub/player/{u}

A 200 body CONFIRMS the account (hard rule #1). 404 means absent.
Chess.com requires a User-Agent; the shared client already sends one.
name and joined are literal API facts.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "chess-profile"
API_URL = "https://api.chess.com/pub/player"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = f"{API_URL}/{username.lower()}"
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

    # the API lowercases usernames in @id/url; confirm on player_id presence
    if not data.get("player_id"):
        return {"pivots": [], "error": None}

    profile_url = data.get("url") or f"https://www.chess.com/member/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"Chess.com: player_id={data.get('player_id')}",
        "status": resp.status_code,
    }]

    name = (data.get("name") or "").strip()
    if name:
        pivots.append({
            "type": "name",
            "value": name,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Chess.com name=\"{name}\"",
            "status": resp.status_code,
        })

    joined = (data.get("joined") or "").strip()
    if joined:
        pivots.append({
            "type": "fact",
            "value": f"chess.com account created {joined[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Chess.com joined=\"{joined[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

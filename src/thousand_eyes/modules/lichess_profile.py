"""Module: lichess-profile.

Keyless Lichess player lookup: GET https://lichess.org/api/user/{u}

A 200 body with a matching id CONFIRMS the account. 404 means absent.
createdAt and the real name in the profile are literal API facts.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "lichess-profile"
API_URL = "https://lichess.org/api/user"


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

    if (data.get("id") or "").lower() != username.lower():
        return {"pivots": [], "error": None}

    profile_url = f"https://lichess.org/@/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"Lichess: id={data.get('id')}",
        "status": resp.status_code,
    }]

    real_name = ((data.get("profile") or {}).get("realName") or "").strip()
    if real_name:
        pivots.append({
            "type": "name",
            "value": real_name,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Lichess profile realName=\"{real_name}\"",
            "status": resp.status_code,
        })

    created = (data.get("createdAt") or "").strip()
    if created:
        pivots.append({
            "type": "fact",
            "value": f"lichess account created {created[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Lichess createdAt=\"{created[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

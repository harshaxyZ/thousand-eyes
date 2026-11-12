"""Module: discogs-profile.

Keyless Discogs user lookup: GET https://api.discogs.com/users/{u}

A 200 body whose username matches exactly CONFIRMS the account. 404 means
absent. Discogs asks for a User-Agent (the shared client sends one).
The profile's website field is a legitimate pivot door.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "discogs-profile"
API_URL = "https://api.discogs.com/users"


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

    if (data.get("username") or "").strip().lower() != username.lower():
        return {"pivots": [], "error": None}

    profile_url = data.get("uri") or f"https://www.discogs.com/user/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"Discogs: username=\"{username}\" (id {data.get('id')})",
        "status": resp.status_code,
    }]

    website = (data.get("website") or "").strip()
    if website.startswith("http"):
        pivots.append({
            "type": "url",
            "value": website,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Discogs website=\"{website}\"",
            "status": resp.status_code,
        })

    released = data.get("num_p/releases")
    if isinstance(released, int) and released > 0:
        pivots.append({
            "type": "fact",
            "value": f"discogs profile lists {released} release(s)",
            "source": MODULE,
            "evidence_url": url,
            "snippet": "Discogs num_p/releases (activity signal)",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

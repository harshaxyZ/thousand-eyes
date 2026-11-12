"""Module: bluesky-profile.

Keyless Bluesky lookup against the public AppView:
GET https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={u}.bsky.social

A 200 JSON body with the profile is the response CONFIRMING the account
exists (hard rule #1). An error payload ("Profile not found") means the
handle is absent - silence, not an error. Rate limits / unexpected
statuses surface as errors (hard rule #7).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "bluesky-profile"
API_URL = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    actor = f"{username}.bsky.social"
    try:
        resp = await client.get(API_URL, params={"actor": actor})
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}

    if resp.status_code != 200:
        return {"pivots": [], "error": None}   # AppView signals absence with 4xx

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    if data.get("error"):                     # {"error": "InvalidRequest", ...}
        return {"pivots": [], "error": None}

    handle = (data.get("handle") or "").strip()
    if handle != actor:
        return {"pivots": [], "error": None}  # body did not confirm the exact handle

    profile_url = f"https://bsky.app/profile/{handle}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": API_URL,
        "snippet": f"Bluesky getProfile: handle=\"{handle}\" (did {data.get('did')})",
        "status": resp.status_code,
    }]

    display = (data.get("displayName") or "").strip()
    if display:
        pivots.append({
            "type": "name",
            "value": display,
            "source": MODULE,
            "evidence_url": API_URL,
            "snippet": f"Bluesky displayName=\"{display}\"",
            "status": resp.status_code,
        })

    created = (data.get("createdAt") or "").strip()
    if created:
        pivots.append({
            "type": "fact",
            "value": f"account created {created[:10]}",
            "source": MODULE,
            "evidence_url": API_URL,
            "snippet": f"Bluesky createdAt=\"{created[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

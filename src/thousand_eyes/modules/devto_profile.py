"""Module: devto-profile.

Keyless DEV Community (dev.to) profile lookup:
GET https://dev.to/api/users/by_username?url={u}

A 200 body whose username matches exactly CONFIRMS the account. 404 means
absent. The profile's own github/twitter/website fields are literal facts
and legitimate pivot doors.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "devto-profile"
API_URL = "https://dev.to/api/users/by_username"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = f"{API_URL}?url={username}"
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

    profile_url = data.get("profile_url") or f"https://dev.to/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"dev.to: username=\"{username}\" (id {data.get('id')})",
        "status": resp.status_code,
    }]

    name = (data.get("name") or "").strip()
    if name:
        pivots.append({
            "type": "name",
            "value": name,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"dev.to name=\"{name}\"",
            "status": resp.status_code,
        })

    for field_name, prefix in (("github_username", "github.com"),
                               ("twitter_username", "x.com"),
                               ("linkedin_username", "linkedin.com/in"),
                               ("website_url", None)):
        raw = (data.get(field_name) or "").strip()
        if not raw:
            continue
        if prefix:
            target = f"https://{prefix}/{raw.lstrip('@')}"
        elif raw.startswith("http"):
            target = raw
        else:
            continue
        pivots.append({
            "type": "url",
            "value": target,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"dev.to {field_name}=\"{raw}\"",
            "status": resp.status_code,
        })

    joined = (data.get("joined_at") or "").strip()
    if joined:
        pivots.append({
            "type": "fact",
            "value": f"dev.to account created {joined[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"dev.to joined_at=\"{joined[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

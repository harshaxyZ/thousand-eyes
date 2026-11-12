"""Module: gitlab-profile.

Keyless GitLab REST lookup against gitlab.com's public API:
GET https://gitlab.com/api/v4/users?username={u}&active=true

A non-empty JSON array whose entry matches the requested username exactly
is the response body CONFIRMING the account exists (hard rule #1: pivots
must be confirmed by the response itself - we never guess). A 200 with an
empty array means the account is absent (silence, not an error). Anything
else (403/429/5xx) is reported as an error, never collapsed into "nothing
found" (hard rule #7).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "gitlab-profile"
API_URL = "https://gitlab.com/api/v4/users"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = API_URL
    try:
        resp = await client.get(url, params={"username": username, "active": "true"})
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

    if not isinstance(data, list):
        return {"pivots": [], "error": f"{MODULE}: unexpected response shape"}

    hit = next((u for u in data
                if isinstance(u, dict) and u.get("username") == username), None)
    if hit is None:
        return {"pivots": [], "error": None}

    profile_url = f"https://gitlab.com/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"GitLab users API: username=\"{username}\" "
                   f"(id {hit.get('id')})",
        "status": resp.status_code,
    }]

    name = (hit.get("name") or "").strip()
    if name:
        pivots.append({
            "type": "name",
            "value": name,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"GitLab profile name=\"{name}\"",
            "status": resp.status_code,
        })

    created = (hit.get("created_at") or "").strip()
    if created:
        pivots.append({
            "type": "fact",
            "value": f"account created {created[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"GitLab created_at=\"{created[:10]}\"",
            "status": resp.status_code,
        })

    email = (hit.get("public_email") or "").strip()
    if email:
        pivots.append({
            "type": "email",
            "value": email,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"GitLab public email=\"{email}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

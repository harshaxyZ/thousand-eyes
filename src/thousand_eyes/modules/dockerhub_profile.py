"""Module: dockerhub-profile.

Keyless Docker Hub profile lookup:
GET https://hub.docker.com/v2/users/{u}

A 200 body whose "orgname"/"username" matches exactly CONFIRMS the account
(hard rule #1). 404 means absent. Redirects are followed per-request (the
endpoint 308s to its canonical form). full_name and date_joined are literal
API facts.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "dockerhub-profile"
API_URL = "https://hub.docker.com/v2/users"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = f"{API_URL}/{username}"
    try:
        resp = await client.get(url, follow_redirects=True)
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

    acct = (data.get("orgname") or data.get("username") or "").strip()
    if acct != username:
        return {"pivots": [], "error": None}

    profile_url = f"https://hub.docker.com/u/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"Docker Hub: account \"{acct}\" (type {data.get('type')})",
        "status": resp.status_code,
    }]

    full = (data.get("full_name") or "").strip()
    if full:
        pivots.append({
            "type": "name",
            "value": full,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Docker Hub full_name=\"{full}\"",
            "status": resp.status_code,
        })

    joined = (data.get("date_joined") or "").strip()
    if joined:
        pivots.append({
            "type": "fact",
            "value": f"docker hub account created {joined[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Docker Hub date_joined=\"{joined[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

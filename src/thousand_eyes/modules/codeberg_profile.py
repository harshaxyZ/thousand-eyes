"""Module: codeberg-profile.

Keyless Codeberg (Forgejo/Gitea) account lookup:
GET https://codeberg.org/api/v1/users/{u}

A 200 body whose login matches exactly CONFIRMS the account. 404 means
absent. full_name / created / website are literal API facts.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "codeberg-profile"
API_URL = "https://codeberg.org/api/v1/users"


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

    login = (data.get("login") or data.get("username") or "").strip()
    if login.lower() != username.lower():
        return {"pivots": [], "error": None}

    profile_url = data.get("html_url") or f"https://codeberg.org/{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": url,
        "snippet": f"Codeberg: login=\"{login}\" (id {data.get('id')})",
        "status": resp.status_code,
    }]

    full = (data.get("full_name") or "").strip()
    if full:
        pivots.append({
            "type": "name",
            "value": full,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Codeberg full_name=\"{full}\"",
            "status": resp.status_code,
        })

    created = (data.get("created") or "").strip()
    if created:
        pivots.append({
            "type": "fact",
            "value": f"codeberg account created {created[:10]}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Codeberg created=\"{created[:10]}\"",
            "status": resp.status_code,
        })

    website = (data.get("website") or "").strip()
    if website.startswith("http"):
        pivots.append({
            "type": "url",
            "value": website,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f"Codeberg website field=\"{website}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

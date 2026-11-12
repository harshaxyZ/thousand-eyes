"""Module: mastodon-lookup.

Keyless Mastodon lookup against mastodon.social's public API:
GET https://mastodon.social/api/v1/accounts/lookup?account={u}@mastodon.social

A 200 JSON body with the account object is the response CONFIRMING the
account exists. A 404 means the handle is absent (silence, not an error).
Rate limits / unexpected statuses surface as errors (hard rule #7).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "mastodon-lookup"
API_URL = "https://mastodon.social/api/v1/accounts/lookup"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    handle = f"{username}@mastodon.social"
    try:
        resp = await client.get(API_URL, params={"account": handle})
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code == 404:
        return {"pivots": [], "error": None}
    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}
    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    acct = (data.get("username") or "").strip()
    if acct != username:
        # the API resolved a redirect/profile alias: body did not confirm the
        # exact handle, so report nothing (never guess, hard rule #1)
        return {"pivots": [], "error": None}

    profile_url = data.get("url") or f"https://mastodon.social/@{username}"
    pivots: list[dict] = [{
        "type": "url",
        "value": profile_url,
        "source": MODULE,
        "evidence_url": API_URL,
        "snippet": f"Mastodon lookup: account=\"{acct}\" (id {data.get('id')})",
        "status": resp.status_code,
    }]

    display = (data.get("display_name") or "").strip()
    if display:
        pivots.append({
            "type": "name",
            "value": display,
            "source": MODULE,
            "evidence_url": API_URL,
            "snippet": f"Mastodon display_name=\"{display}\"",
            "status": resp.status_code,
        })

    created = (data.get("created_at") or "").strip()
    if created:
        pivots.append({
            "type": "fact",
            "value": f"account created {created[:10]}",
            "source": MODULE,
            "evidence_url": API_URL,
            "snippet": f"Mastodon created_at=\"{created[:10]}\"",
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

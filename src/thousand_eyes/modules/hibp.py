"""Module 4: hibp.

Optional key (HIBP_API_KEY env var). A breach name in the JSON is a leaf
with a date, UNLESS the payload includes a username field, which makes it
a pivot. No key -> skip, print "skipped". Never pulls breach dumps, never
attempts to deanonymise anything — only calls the "breaches for account"
endpoint, which returns breach names/dates, not personal data.
"""

from __future__ import annotations

import os

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "hibp"
API_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{account}"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "leaves": [...], "error": str|None, "skipped": str|None}."""
    api_key = os.environ.get("HIBP_API_KEY")
    if not api_key:
        return {"pivots": [], "leaves": [], "error": None, "skipped": "no HIBP_API_KEY set (free account-level check stays a manual browser step)"}

    url = API_URL.format(account=email)
    try:
        resp = await client.get(
            url,
            headers={"hibp-api-key": api_key},
            params={"truncateResponse": "false"},
        )
    except httpx.HTTPError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: {describe_http_error(exc)}", "skipped": None}

    if resp.status_code == 404:
        return {"pivots": [], "leaves": [], "error": None, "skipped": None}

    if resp.status_code == 401:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: invalid HIBP_API_KEY (status 401)", "skipped": None}

    if resp.status_code != 200:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: unexpected status {resp.status_code}", "skipped": None}

    try:
        breaches = resp.json()
    except ValueError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: invalid json response ({exc})", "skipped": None}

    pivots: list[dict] = []
    leaves: list[dict] = []

    for b in breaches:
        name = b.get("Name") or b.get("Title") or "unknown-breach"
        date = b.get("BreachDate", "unknown-date")
        username_field = b.get("Username") or b.get("username")
        if username_field:
            pivots.append({
                "type": "handle",
                "value": username_field,
                "source": MODULE,
                "evidence_url": url,
                "snippet": f'breach="{name}" username="{username_field}"',
                "status": resp.status_code,
            })
        else:
            leaves.append({"site": f"{name} ({date})", "url": url, "status": resp.status_code, "category": "breach"})

    return {"pivots": pivots, "leaves": leaves, "error": None, "skipped": None}

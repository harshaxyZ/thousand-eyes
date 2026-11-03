"""Module 2: gravatar.

MD5 of the trimmed lowercase email. GET the profile json. A 200 with a
display name or a list of accounts is a pivot. A 404 is silence, not an
error — Gravatar's 404 for an unregistered hash is expected, routine
behaviour, not a failure of the request.
"""

from __future__ import annotations

import hashlib

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "gravatar"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}.

    Each pivot: {"type", "value", "source", "evidence_url", "snippet", "status"}
    """
    digest = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    url = f"https://www.gravatar.com/{digest}.json"

    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code == 404:
        return {"pivots": [], "error": None}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    entries = data.get("entry") or []
    pivots: list[dict] = []

    for entry in entries:
        display_name = entry.get("displayName")
        if display_name:
            pivots.append({
                "type": "name",
                "value": display_name,
                "source": MODULE,
                "evidence_url": url,
                "snippet": f'displayName="{display_name}"',
                "status": resp.status_code,
            })

        for acc in entry.get("accounts") or []:
            acc_url = acc.get("url")
            if acc_url and acc_url.startswith("https://"):
                pivots.append({
                    "type": "url",
                    "value": acc_url,
                    "source": MODULE,
                    "evidence_url": url,
                    "snippet": f'accounts[].url="{acc_url}" (shortname={acc.get("shortname")})',
                    "status": resp.status_code,
                })

    if not pivots:
        # 200 with no usable identifiers is still a confirmed profile — but
        # per hard rule #1 (no evidence, no line) we don't invent a finding
        # from an empty entry list.
        return {"pivots": [], "error": None}

    return {"pivots": pivots, "error": None}

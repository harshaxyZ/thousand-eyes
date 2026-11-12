"""Module: archive-creator.

Keyless Internet Archive creator search:
GET https://archive.org/advancedsearch.php?q=creator:"{u}"&output=json

A hit CONFIRMS that uploads are credited to that creator NAME - the name is
NOT unique, so the pivot is reported as an unconfirmed name match and the
items themselves are the confirmed evidence (hard rule #1).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "archive-creator"
API_URL = "https://archive.org/advancedsearch.php"


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    params = {"q": f'creator:"{username}"', "output": "json", "rows": "3",
              "fl[]": "identifier,title,creator,date"}
    try:
        resp = await client.get(API_URL, params=params)
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

    body = data.get("response") or {}
    found = int(body.get("numFound") or 0)
    docs = body.get("docs") or []
    if not found or not docs:
        return {"pivots": [], "error": None}

    pivots: list[dict] = []
    for doc in docs[:3]:
        if not isinstance(doc, dict):
            continue
        ident = str(doc.get("identifier") or "").strip()
        if not ident:
            continue
        pivots.append({
            "type": "url",
            "value": f"https://archive.org/details/{ident}",
            "source": MODULE,
            "evidence_url": str(resp.url),
            "snippet": f"Internet Archive: creator match, item \"{doc.get('title') or ident}\"",
            "status": resp.status_code,
        })

    pivots.append({
        "type": "fact",
        "value": f"internet archive lists {found} upload(s) credited to \"{username}\"",
        "source": MODULE,
        "evidence_url": str(resp.url),
        "snippet": "creator name match only - names are not unique, treat as unconfirmed",
        "status": resp.status_code,
    })
    return {"pivots": pivots, "error": None}

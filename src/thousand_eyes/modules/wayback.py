"""wayback - Internet Archive CDX lookup (keyless, verified 2026-11-03).

  GET https://web.archive.org/cdx/search/cdx?url={host}/{path*}&output=json&fl=timestamp,original,statuscode&collapse=urlkey&limit=N

For a URL lead we query snapshots of that exact URL; for a domain lead we
query the wildcard host. Snapshot URLs that differ from the live URL become
url pivots the engine may fetch at the next depth - archived pages are a
classic wildfire spread (deleted bios, moved profiles).
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error
from urllib.parse import quote

MODULE = "wayback"
CDX_URL = "https://web.archive.org/cdx/search/cdx"


def _parse_rows(data) -> list[dict]:
    if not isinstance(data, list) or len(data) < 2:
        return []
    header = data[0]
    rows = []
    for row in data[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            continue
        rows.append({header[i]: row[i] for i in range(len(header))})
    return rows


async def run(client: httpx.AsyncClient, value: str, *, is_domain: bool = False, limit: int = 8) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    template = value if is_domain else value.replace("https://", "").replace("http://", "")
    match_type = "domain" if is_domain else "prefix"
    params = {
        "url": template,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "collapse": "urlkey",
        "limit": str(limit),
        "matchType": match_type,
    }
    try:
        resp = await client.get(CDX_URL, params=params)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        rows = _parse_rows(resp.json())
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    pivots: list[dict] = []
    evidence = f"{CDX_URL}?url={quote(template)}"
    for row in rows:
        original = row.get("original") or ""
        if not original:
            continue
        statuscode = row.get("statuscode")
        pivots.append({
            "type": "url",
            "value": f"https://web.archive.org/web/{row.get('timestamp')}/{original}",
            "source": MODULE,
            "evidence_url": evidence,
            "snippet": f"archived snapshot of {original} (status {statuscode})",
            "status": int(statuscode) if str(statuscode).isdigit() else None,
        })
    return {"pivots": pivots, "error": None}

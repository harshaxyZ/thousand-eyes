"""pgp - keys.openpgp.org vks lookup, keyless (verified 2026-11-03).

  GET https://keys.openpgp.org/vks/v1/by-email/<email>
  → 200 armored key body = the email is anchored to a real PGP key
       (evidence carries creation date parsed from the armor)
  → 404 "No key found for email address ..." = a well-formed negative

A key hit is a pivot (type "url") pointing at the key page; a 404 is
silence, not an error.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "pgp"
API_URL = "https://keys.openpgp.org/vks/v1/by-email/{email}"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    url = API_URL.format(email=email)
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code == 404:
        return {"pivots": [], "error": None}
    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    body = resp.text or ""
    created = ""
    for line in body.splitlines():
        if line.strip().startswith(":") and "created" in line.lower():
            created = line.strip().split(":", 1)[1].strip()
            break

    snippet = "PGP key exists for email on keys.openpgp.org"
    if created:
        snippet += f" (created=___{created}___)"
    return {
        "pivots": [{
            "type": "url",
            "value": f"https://keys.openpgp.org/search?q={email}",
            "source": MODULE,
            "evidence_url": url,
            "snippet": snippet,
            "status": resp.status_code,
        }],
        "error": None,
    }

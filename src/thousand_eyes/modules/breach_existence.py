"""breach-existence - the keyless "is my email in public leaks" layer.

Primary (keyless, verified live 2026-11-03):
  GET https://api.xposedornot.com/v1/check-email/<email>
  → clean:    {"Error":"Not found","email":null}
  → exposed:  {"breaches_details":{...}, ...}  (breach NAMES only - this is
    existence-attribution, never dump contents)

Stealer logs (keyless, verified live 2026-11-13): Hudson Rock's free
osint-tools endpoint
  GET https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email?email=<email>
  -> {"message": "...", "stealers": [{"date_compromised", "computer_name",
      "operating_system", "total_user_services", ...}]}
It says WHETHER an infostealer-infected machine held credentials for the
address, and when. It never returns the credentials. HUDSON_API_KEY is
optional: when set it is sent along, nothing else changes.
"""

from __future__ import annotations

import os

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "breach-existence"
XON_URL = "https://api.xposedornot.com/v1/check-email/{email}"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "leaves": [...], "error": str|None}.

    A confirmed breach hit is a LEAF (site=breach-name, evidence=url) - it
    confirms existence, it is not a new identifier to expand.
    """
    url = XON_URL.format(email=email)
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code != 200:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}: invalid json response ({exc})"}

    leaves: list[dict] = []
    details = (data.get("breaches_details") or {})
    breach_map = details.get("breaches") or {}

    if isinstance(breach_map, dict) and breach_map:
        for breach_name, meta in breach_map.items():
            site_label = breach_name
            if isinstance(meta, dict) and meta.get("breach_date"):
                site_label = f"{breach_name} ({meta['breach_date']})"
            leaves.append({"site": site_label, "url": url, "status": resp.status_code, "category": "breach"})

    return {"pivots": [], "leaves": leaves, "error": None}


HUDSON_URL = "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email"


async def run_hudsonrock(client: httpx.AsyncClient, email: str) -> dict:
    """Keyless stealer-log existence check. A blocked or failing request is an
    error, never a clean bill (hard rule #7)."""
    key = os.environ.get("HUDSON_API_KEY")
    headers = {"apikey": key} if key else None
    try:
        resp = await client.get(HUDSON_URL, params={"email": email}, headers=headers)
    except httpx.HTTPError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: {describe_http_error(exc)}"}
    if resp.status_code != 200:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: unexpected status {resp.status_code}"}
    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: invalid json ({exc})"}
    if not isinstance(data, dict):
        return {"pivots": [], "leaves": [], "error": f"{MODULE}:hudsonrock: unexpected response shape"}
    leaves: list[dict] = []
    evidence = f"{HUDSON_URL}?email={email}"
    # "stealers" is the live shape; "stealfinder" was the older one
    logs = data.get("stealers") or data.get("stealfinder") or []
    if isinstance(logs, list):
        for t in logs:
            if not isinstance(t, dict):
                continue
            host = t.get("computer_name") or t.get("machine_name") or "unknown host"
            label = f"stealer log: {host}"
            when = str(t.get("date_compromised") or "")[:10]
            if when:
                label += f" (compromised {when})"
            services = t.get("total_user_services")
            if services:
                label += f" - {services} saved login(s) at risk"
            leaves.append({"site": label, "url": evidence,
                           "status": resp.status_code, "category": "stealer"})
    return {"pivots": [], "leaves": leaves, "error": None}

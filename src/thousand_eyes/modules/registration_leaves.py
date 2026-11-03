"""Module 5: registration leaves.

Loads the hand-written YAML site list (data/registration_sites.yaml).
Each entry has a URL template + "registered" marker + "absent" marker.
If both match or neither matches, the site is dropped (ambiguous).
Confirmed leaves never pivot and are never re-expanded (hard rule #4).

Entries with marker "__SKIP__" are treated as disabled (no live, reliable
anonymous existence-check endpoint known) and are skipped without a request.
"""

from __future__ import annotations

from importlib import resources
from urllib.parse import quote

import httpx
import yaml

from thousand_eyes.http_client import describe_http_error

MODULE = "registration-leaves"


def _load_sites() -> list[dict]:
    data_text = resources.files("thousand_eyes.data").joinpath("registration_sites.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(data_text) or {}
    return parsed.get("sites") or []


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"leaves": [...], "errors": [...]}.

    Each leaf: {"site", "url", "status"}.
    Each error: {"module", "error"}.
    """
    sites = _load_sites()
    leaves: list[dict] = []
    errors: list[dict] = []

    for site in sites:
        name = site.get("name", "unknown")
        registered_marker = site.get("registered_marker")
        absent_marker = site.get("absent_marker")

        if registered_marker == "__SKIP__" or absent_marker == "__SKIP__":
            continue

        raw_template = site.get("url_template", "")
        try:
            url = raw_template.format(value=quote(email))
        except (KeyError, IndexError, ValueError):
            # malformed template in the YAML -- drop the site, never crash the scan
            continue

        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            errors.append({"module": f"{MODULE}:{name}", "error": describe_http_error(exc)})
            continue

        body = resp.text
        has_registered = registered_marker in body
        has_absent = absent_marker in body

        if has_registered and has_absent:
            continue  # ambiguous, drop
        if not has_registered and not has_absent:
            continue  # neither matched, drop

        if has_registered:
            leaves.append({"site": name, "url": url, "status": resp.status_code})
        # absent: not registered, no leaf, nothing to print

    return {"leaves": leaves, "errors": errors}

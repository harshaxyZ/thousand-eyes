"""Module 7: fetch.

GET only URLs discovered in steps 2-4 (gravatar, exact-document, hibp),
same strict extraction rules as exact-document. No further crawling beyond
depth 2 — the caller controls depth, this module just does one GET + one
extraction pass per URL.
"""

from __future__ import annotations

import httpx

from thousand_eyes.extract import extract_emails, extract_phones, extract_urls, visible_text
from thousand_eyes.http_client import describe_http_error

MODULE = "fetch"


async def run(client: httpx.AsyncClient, url: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}.

    Fetches `url` once and extracts https URLs / emails / E.164 phones found
    on the page. Does not re-fetch or follow anything further — depth
    control lives in the caller (scan.py).
    """
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {url}: {describe_http_error(exc)}"}

    if resp.status_code != 200:
        return {"pivots": [], "error": None}

    body = visible_text(resp.text)
    pivots: list[dict] = []

    for found_url in extract_urls(body):
        if found_url == url:
            continue
        pivots.append({
            "type": "url",
            "value": found_url,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'https URL found on page {url}',
            "status": resp.status_code,
        })

    for email in extract_emails(body):
        pivots.append({
            "type": "email",
            "value": email,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'email found on page {url}',
            "status": resp.status_code,
        })

    for phone in extract_phones(body):
        pivots.append({
            "type": "phone",
            "value": phone,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'E.164 phone found on page {url}',
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

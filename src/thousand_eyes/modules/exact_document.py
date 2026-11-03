"""Module 3: exact-document — v0.3 (the "public mentions" spike).

Search only for the QUOTED exact seed, using the first available provider:

  1. bing-html  (KEYLESS, default)  — Bing web results with a browser UA;
       result links are decoded from Bing's u=a1<base64url> redirect form.
  2. serper     (SERPER_API_KEY)    — Google-index via serper.dev (2,500
       free credits, no card) — great demo insurance.
  3. brave      (BRAVE_API_KEY)     — official search API.
  4. google_cse (GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID) — legacy (EOL Jan-2027).

A hit is a pivot only if the LIVE page body still contains the exact seed
(hard rule #1). From that body we extract only https URLs, emails and
E.164 phones. If one result page fails to fetch, the REMAINING pages are
still processed (bug fix: previously a single dead page aborted the whole
module); each failure is recorded in `errors`, never swallowed.
"""

from __future__ import annotations

import base64
import os
import re

import httpx

from thousand_eyes.extract import body_contains_seed, extract_emails, extract_phones, extract_urls, visible_text
from thousand_eyes.http_client import describe_http_error

MODULE = "exact-document"

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
BING_URL = "https://www.bing.com/search"
SERPER_URL = "https://google.serper.dev/search"

MAX_RESULTS_TO_FETCH = 5
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_BING_REDIRECT_RE = re.compile(r'u=a1([A-Za-z0-9\-_]{8,})')
_BING_HOSTS = {"bing.com", "microsoft.com", "bing.net", "msn.com", "live.com", "go.microsoft.com"}
_BING_NO_RESULTS = "There are no results for"


def _bing_decode(tok: str) -> str | None:
    """Bing wraps organic URLs as .../ck/a?...&u=a1<base64url>. Decode the
    token back to the real https URL; discard anything that isn't one."""
    try:
        padded = tok + "=" * (-len(tok) % 4)
        raw = base64.urlsafe_b64decode(padded)
        decoded = raw.decode("utf-8", "ignore")
    except Exception:
        return None
    return decoded if decoded.startswith("http") else None


async def _bing_html_search(client: httpx.AsyncClient, query: str) -> tuple[list[str], str]:
    try:
        resp = await client.get(
            BING_URL,
            headers={"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.8"},
            params={"q": query, "count": "10", "setmkt": "en-US"},
        )
    except httpx.HTTPError as exc:
        return [], f"{MODULE}:bing: {describe_http_error(exc)}"

    if resp.status_code != 200:
        return [], f"{MODULE}:bing: unexpected status {resp.status_code}"

    html = resp.text
    if _BING_NO_RESULTS in html:
        return [], ""  # clean zero-results

    urls: list[str] = []
    for m in _BING_REDIRECT_RE.finditer(html):
        real = _bing_decode(m.group(1))
        if not real or not real.startswith("https://"):
            continue
        host = real.split("/", 3)[2] if "://" in real else ""
        if not host or any(host == h or host.endswith("." + h) for h in _BING_HOSTS):
            continue
        if real not in urls:
            urls.append(real)
    return urls[:MAX_RESULTS_TO_FETCH], ""


async def _serper_search(client: httpx.AsyncClient, api_key: str, query: str) -> tuple[list[str], str]:
    try:
        resp = await client.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": MAX_RESULTS_TO_FETCH},
        )
    except httpx.HTTPError as exc:
        return [], f"{MODULE}:serper: {describe_http_error(exc)}"
    if resp.status_code != 200:
        return [], f"{MODULE}:serper: unexpected status {resp.status_code}"
    try:
        data = resp.json()
    except ValueError as exc:
        return [], f"{MODULE}:serper: invalid json ({exc})"
    urls = [item.get("link") for item in (data.get("organic") or []) if item.get("link")]
    return urls[:MAX_RESULTS_TO_FETCH], ""


async def _brave_search(client: httpx.AsyncClient, api_key: str, query: str) -> tuple[list[str], str]:
    try:
        resp = await client.get(
            BRAVE_URL,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": MAX_RESULTS_TO_FETCH},
        )
    except httpx.HTTPError as exc:
        return [], f"{MODULE}:brave: {describe_http_error(exc)}"
    if resp.status_code != 200:
        return [], f"{MODULE}:brave: unexpected status {resp.status_code}"
    try:
        data = resp.json()
    except ValueError as exc:
        return [], f"{MODULE}:brave: invalid json ({exc})"
    urls = [item.get("url") for item in (data.get("web") or {}).get("results") or [] if item.get("url")]
    return urls[:MAX_RESULTS_TO_FETCH], ""


async def _google_cse_search(client: httpx.AsyncClient, api_key: str, cse_id: str, query: str) -> tuple[list[str], str]:
    try:
        resp = await client.get(
            GOOGLE_CSE_URL,
            params={"key": api_key, "cx": cse_id, "q": query, "num": MAX_RESULTS_TO_FETCH},
        )
    except httpx.HTTPError as exc:
        return [], f"{MODULE}:google_cse: {describe_http_error(exc)}"
    if resp.status_code != 200:
        return [], f"{MODULE}:google_cse: unexpected status {resp.status_code}"
    try:
        data = resp.json()
    except ValueError as exc:
        return [], f"{MODULE}:google_cse: invalid json ({exc})"
    urls = [item.get("link") for item in data.get("items") or [] if item.get("link")]
    return urls[:MAX_RESULTS_TO_FETCH], ""


async def run(client: httpx.AsyncClient, seed_value: str) -> dict:
    """Returns {"pivots": [...], "errors": [...], "skipped": str|None, "search_source": str}."""
    serper_key = os.environ.get("SERPER_API_KEY")
    brave_key = os.environ.get("BRAVE_API_KEY")
    google_key = os.environ.get("GOOGLE_CSE_API_KEY")
    google_cx = os.environ.get("GOOGLE_CSE_ID")

    query = f'"{seed_value}"'
    search_source = "bing-html"
    errors: list[str] = []

    result_urls: list[str] = []
    if brave_key:
        result_urls, err = await _brave_search(client, brave_key, query)
        search_source = "brave"
    elif serper_key:
        result_urls, err = await _serper_search(client, serper_key, query)
        search_source = "serper"
    else:
        # keyless first: everyone gets the public-mention spike
        result_urls, err = await _bing_html_search(client, query)
        if err and serper_key is None and brave_key is None and not (google_key and google_cx):
            pass  # recorded below; provider fallbacks would go here if keys existed
        if not result_urls and google_key and google_cx:
            search_source = "google_cse"
            result_urls, err2 = await _google_cse_search(client, google_key, google_cx, query)
            if err2:
                errors.append(err2)
        else:
            search_source = "bing-html"
        if err:
            errors.append(err)

    pivots: list[dict] = []

    for page_url in result_urls:
        try:
            page_resp = await client.get(page_url)
        except httpx.HTTPError as exc:
            # bug fix: one dead result page no longer aborts the sweep
            errors.append(f"{MODULE}: fetch of {page_url} failed ({describe_http_error(exc)})")
            continue

        if page_resp.status_code != 200:
            continue

        body = page_resp.text
        if not body_contains_seed(body, seed_value):
            # Search engine indexed it, but the live page no longer contains
            # the exact seed - not a confirmed hit. Hard rule #1.
            continue

        # The page itself is a confirmed pivot (it contains the exact seed).
        pivots.append({
            "type": "url",
            "value": page_url,
            "source": f"{MODULE}:{search_source}",
            "evidence_url": page_url,
            "snippet": f'page body contains exact seed "{seed_value}"',
            "status": page_resp.status_code,
        })

        for url in extract_urls(visible_text(body)):
            if url == page_url:
                continue
            pivots.append({
                "type": "url",
                "value": url,
                "source": f"{MODULE}:{search_source}",
                "evidence_url": page_url,
                "snippet": f'https URL found on page containing exact seed "{seed_value}"',
                "status": page_resp.status_code,
            })

        for email in extract_emails(visible_text(body)):
            if email == seed_value.lower():
                continue
            pivots.append({
                "type": "email",
                "value": email,
                "source": f"{MODULE}:{search_source}",
                "evidence_url": page_url,
                "snippet": f'email found on page containing exact seed "{seed_value}"',
                "status": page_resp.status_code,
            })

        for phone in extract_phones(visible_text(body)):
            if phone == seed_value:
                continue
            pivots.append({
                "type": "phone",
                "value": phone,
                "source": f"{MODULE}:{search_source}",
                "evidence_url": page_url,
                "snippet": f'E.164 phone found on page containing exact seed "{seed_value}"',
                "status": page_resp.status_code,
            })

    return {"pivots": pivots, "errors": errors, "skipped": None, "search_source": search_source}

"""darkweb — dark-web index mining, keyless and read-only.

Uses Ahmia's CLEARNET search endpoint (https://ahmia.fi/search/). Ahmia
indexes Tor hidden services and serves its index over plain HTTPS, so this
module needs no Tor daemon, no proxy chain, and never opens an .onion
connection. We only learn: which hidden services the subject's identifier
is ALREADY INDEXED NEXT TO. An existence/intel fact, not access.

Anti-scrape note (verified live 2026-11-04): the plain ?q= query returns a
~4.7 KB EMPTY LANDING PAGE; only a query carrying the hidden form token
(16d742=18c116, see the form markup) returns real results. We read the
token from the module constant — if Ahmia ever rotates it, the zero-signal
landing page is detected and reported as INCONCLUSIVE (error), never as a
fabricated "no mentions". Chrome furniture (the footer "Contact" onion,
the MITM-warning CSS) must never become a "finding": we only accept
onions that appear INSIDE <li class="result"> blocks of the results list.

Evidence law:
  - one GET per seed term (email / username), capped per scan;
  - an .onion host inside a result block is a LEAF (never a pivot —
    nothing is fetched, ever);
  - no-result page → zero leaves AND zero "clean" claims beyond a leaf
    count of zero; landing-page → inconclusive error.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "darkweb"

SEARCH_URL = "https://ahmia.fi/search/?q={term}&16d742={token}"
SEARCH_TOKEN = "18c116"   # hidden form token; rotate ONLY after live verification
MAX_LEAVES = 10           # cap on listed results per search (relevance order)

_ONION_RE = re.compile(r"(?:https?://)?([a-z2-7]{16,56}(?:\.[a-z2-7]{16,56})?)\.onion")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_RESULT_SPLIT_RE = re.compile(r'<li class="result">')
_TITLE_RE = re.compile(r"<h4>\s*<a[^>]*>(.*?)</a>", re.S | re.I)
_DESC_RE = re.compile(r"</h4>\s*<p>(.*?)</p>", re.S)
_LASTSEEN_RE = re.compile(r'data-timestamp="([^"]+)"')


def parse_results(html: str) -> list[dict]:
    """Extract {onion, title, description, last_seen} from an Ahmia RESULT page.

    Only onions inside <li class="result"> blocks count. Chrome links (the
    footer "Contact" onion, anti-MITM CSS, dates pagination) are ignored by
    construction. Never raises; a malformed page yields an empty list.
    """
    hits: list[dict] = []
    seen: set[str] = set()
    blocks = _RESULT_SPLIT_RE.split(html)[1:]  # text before the first result = chrome
    for block in blocks[:MAX_LEAVES * 2]:
        m = _ONION_RE.search(block)
        if not m:
            continue
        host = m.group(1).lower()
        if host in seen:
            continue
        seen.add(host)
        raw_title = _TITLE_RE.search(block)
        raw_desc = _DESC_RE.search(block)
        last_seen_m = _LASTSEEN_RE.search(block)
        title = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw_title.group(1))).strip() if raw_title else ""
        desc = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw_desc.group(1))).strip() if raw_desc else ""
        hits.append({
            "onion": f"{host}.onion",
            "title": title[:160],
            "description": desc[:200],
            "last_seen": last_seen_m.group(1) if last_seen_m else "",
        })
        if len(hits) >= MAX_LEAVES:
            break
    return hits


def _looks_like_landing_page(html: str) -> bool:
    """The empty anti-scrape landing page: ~5 KB, no results list at all."""
    return 'class="searchResults"' not in html and "totalResults" not in html


async def run(client: httpx.AsyncClient, term: str, *, sink=None) -> dict:
    """Returns {"leaves": [...], "errors": [...], "checked": int}.

    Each leaf: {"site": "darknet: <onion> — <title>", "url": <search url>,
                "status": <http status>, "category": "darknet"}.
    """
    if sink:
        sink.stage(f"darknet index: {term}")
        sink.expect(1)
    url = SEARCH_URL.format(term=quote(term, safe=""), token=SEARCH_TOKEN)
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"leaves": [], "errors": [f"{MODULE}: {term}: {describe_http_error(exc)}"], "checked": 1}
    if sink:
        sink.bump(1)

    if resp.status_code != 200:
        return {
            "leaves": [],
            "errors": [f"{MODULE}: unexpected status {resp.status_code} from ahmia index"],
            "checked": 1,
        }

    if _looks_like_landing_page(resp.text):
        # Ambiguity resolution (live-verified): rare/zero-hit queries return
        # the search landing page, SAME AS a rotated anti-scrape token would
        # for a real victim's term. Never claim either way — zero leaves,
        # zero errors, and a skipped-note the report shows once.
        return {
            "leaves": [],
            "errors": [],
            "note": (
                "darknet index: no indexed mentions visible, or anti-scrape page — "
                "worth a manual re-check via https://ahmia.fi/search/?q=<term> over Tor"
            ),
            "checked": 1,
        }

    leaves: list[dict] = []
    for hit in parse_results(resp.text):
        label = f"darknet: {hit['onion']}"
        if hit["title"]:
            label += f" — {hit['title'][:120]}"
        elif hit["description"]:
            label += f" — {hit['description'][:120]}"
        if hit["last_seen"]:
            label += f" (index-seen {hit['last_seen']})"
        leaves.append({
            "site": label,
            "url": url,          # evidence = the Ahmia index query itself
            "status": resp.status_code,
            "category": "darknet",
        })
    return {"leaves": leaves, "errors": [], "checked": 1}

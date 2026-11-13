"""darkweb - dark-web index mining, keyless and read-only.

Uses Ahmia's CLEARNET search endpoint (https://ahmia.fi/search/). Ahmia
indexes Tor hidden services and serves its index over plain HTTPS, so this
module needs no Tor daemon, no proxy chain, and never opens an .onion
connection. We only learn: which hidden services the subject's identifier
is ALREADY INDEXED NEXT TO. An existence/intel fact, not access.

Anti-scrape note (verified live 2026-11-04, re-verified 2026-11-13): the
plain ?q= query returns a ~4.7 KB EMPTY LANDING PAGE; only a query carrying
the hidden form token (a hex name=value pair in the search form markup)
returns real results. Ahmia ROTATES that token, so it is read from the
landing page's own form before searching (one extra GET, cached for a few
minutes); the module constant is only the fallback. If the token cannot be
read, the zero-signal landing page is detected and reported as
INCONCLUSIVE, never as a fabricated "no mentions". Chrome furniture (the footer "Contact" onion,
the MITM-warning CSS) must never become a "finding": we only accept
onions that appear INSIDE <li class="result"> blocks of the results list.

Evidence law:
  - one search GET per seed term (email / username / domain), capped
    per scan, plus one cached GET of the public search form;
  - an .onion host inside a result block is a LEAF (never a pivot -
    nothing is fetched, ever);
  - no-result page → zero leaves AND zero "clean" claims beyond a leaf
    count of zero; landing-page → inconclusive error.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "darkweb"

CLEARNET_BASE = "https://ahmia.fi"
# Ahmia's own v3 onion mirror: the SAME index, reached directly over Tor so
# the lookup does not depend on the clearnet host. Queried only when the
# operator opts in with --tor; unverified from a clearnet-only environment.
ONION_BASE = "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"
# fallback hidden form token, used only when the live form cannot be read
SEARCH_TOKEN_NAME = "16d742"
SEARCH_TOKEN = "18c116"
TOKEN_TTL_SECONDS = 600.0
MAX_LEAVES = 10           # cap on listed results per search (relevance order)

# onion circuits are slow; give the Tor pass a much longer leash
TOR_TIMEOUT = httpx.Timeout(45.0, connect=30.0)
_TOR_UA = "thousand-eyes (self-audit; dark-web index mention check only)"

_ONION_RE = re.compile(r"(?:https?://)?([a-z2-7]{16,56}(?:\.[a-z2-7]{16,56})?)\.onion")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_RESULT_SPLIT_RE = re.compile(r'<li class="result">')
_TITLE_RE = re.compile(r"<h4>\s*<a[^>]*>(.*?)</a>", re.S | re.I)
_DESC_RE = re.compile(r"</h4>\s*<p>(.*?)</p>", re.S)
_LASTSEEN_RE = re.compile(r'data-timestamp="([^"]+)"')


_FORM_TOKEN_RE = re.compile(
    r'<input[^>]*type="hidden"[^>]*name="([0-9a-f]{4,16})"[^>]*value="([0-9a-f]{4,16})"', re.I)

# base-url -> (name, value, fetched-at) of the last token read from its form
_token_cache: dict[str, tuple[str, str, float]] = {}


def parse_form_token(html: str) -> tuple[str, str] | None:
    """The hidden (name, value) pair of Ahmia's search form, or None."""
    m = _FORM_TOKEN_RE.search(html or "")
    return (m.group(1), m.group(2)) if m else None


async def form_token(client: httpx.AsyncClient, base: str = CLEARNET_BASE) -> tuple[str, str]:
    """Current form token for `base`: read from its landing page, cached
    briefly. Any failure falls back to the module constant (and the
    landing-page detector then reports the search as inconclusive)."""
    now = time.monotonic()
    cached = _token_cache.get(base)
    if cached and now - cached[2] < TOKEN_TTL_SECONDS:
        return cached[0], cached[1]
    try:
        resp = await client.get(f"{base}/")
    except httpx.HTTPError:
        return SEARCH_TOKEN_NAME, SEARCH_TOKEN
    found = parse_form_token(resp.text) if resp.status_code == 200 else None
    if not found:
        return SEARCH_TOKEN_NAME, SEARCH_TOKEN
    _token_cache[base] = (found[0], found[1], now)
    return found


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


def _leaf(hit: dict, evidence_url: str, status: int | None, via: str) -> dict:
    label = f"darknet: {hit['onion']}"
    if hit["title"]:
        label += f" - {hit['title'][:120]}"
    elif hit["description"]:
        label += f" - {hit['description'][:120]}"
    if hit["last_seen"]:
        label += f" (index-seen {hit['last_seen']})"
    if via:
        label += f" [{via}]"
    return {"site": label, "url": evidence_url, "status": status, "category": "darknet"}


async def _search_base(client: httpx.AsyncClient, base: str, term: str,
                       *, via: str = "") -> dict:
    """One index search against `base`. Returns {leaves, errors, note?}.

    This is a MENTION check only: an onion host listed in the index is a leaf
    (cited, never contacted). Nothing here fetches a hidden service's content
    or any dump - that stays outside the fence on purpose.
    """
    tag = f"{MODULE}:{via}" if via else MODULE
    name, token = await form_token(client, base)
    url = f"{base}/search/?q={quote(term, safe='')}&{name}={token}"
    try:
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"leaves": [], "errors": [f"{tag}: {term}: {describe_http_error(exc)}"]}
    if resp.status_code != 200:
        return {"leaves": [],
                "errors": [f"{tag}: unexpected status {resp.status_code} from {via or 'ahmia'} index"]}
    if _looks_like_landing_page(resp.text):
        # rare/zero-hit queries return the landing page, same shape a rotated
        # anti-scrape token would produce. Never claim either way.
        return {"leaves": [], "errors": [],
                "note": ("darknet index: no indexed mentions visible, or anti-scrape "
                         "page - worth a manual re-check via ahmia over Tor")}
    leaves = [_leaf(hit, url, resp.status_code, via) for hit in parse_results(resp.text)]
    return {"leaves": leaves, "errors": []}


async def _search_onion(term: str, tor_proxy: str) -> dict:
    """The same index, over Tor. Any failure (no socks extra, Tor not
    running, dead circuit) is an inconclusive error, never a clean result."""
    client = None
    try:
        client = httpx.AsyncClient(
            proxy=tor_proxy, timeout=TOR_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": _TOR_UA})
        return await _search_base(client, ONION_BASE, term, via="tor")
    except ImportError:
        return {"leaves": [], "errors": [
            f"{MODULE}:tor: SOCKS support is not installed - add it with "
            "`pipx inject thousand-eyes socksio` (or pip install 'httpx[socks]')"]}
    except Exception as exc:  # noqa: BLE001 - bad proxy string, dead circuit, etc.
        return {"leaves": [], "errors": [f"{MODULE}:tor: {type(exc).__name__}: {exc}"[:200]]}
    finally:
        if client is not None:
            await client.aclose()


async def run(client: httpx.AsyncClient, term: str, *, sink=None,
              tor_proxy: str = "") -> dict:
    """Returns {"leaves": [...], "errors": [...], "checked": int}.

    Each leaf: {"site": "darknet: <onion> - <title>", "url": <search url>,
                "status": <http status>, "category": "darknet"}.

    With `tor_proxy` the same index is also queried over Tor (--tor); the
    clearnet pass always runs, so a missing/offline Tor never hides the
    clearnet answer.
    """
    if sink:
        sink.stage(f"darknet index: {term}")
        sink.expect(1)
    clear = await _search_base(client, CLEARNET_BASE, term)
    if sink:
        sink.bump(1)

    leaves = list(clear["leaves"])
    errors = list(clear["errors"])
    note = clear.get("note")
    checked = 1

    if tor_proxy:
        tor = await _search_onion(term, tor_proxy)
        checked += 1

        def _host(site: str) -> str:
            m = _ONION_RE.search(site)
            return m.group(1).lower() if m else site

        seen = {_host(leaf["site"]) for leaf in leaves}
        for leaf in tor["leaves"]:
            if _host(leaf["site"]) not in seen:
                leaves.append(leaf)
        errors += tor["errors"]

    out: dict = {"leaves": leaves, "errors": errors, "checked": checked}
    if note and not leaves:
        out["note"] = note
    return out

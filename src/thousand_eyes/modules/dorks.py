"""web-dorks - search-engine dorking for one identifier.

A dork is an exact-phrase query narrowed with search operators (site:,
filetype:, inurl:, ext:). This module expands ONE lead into a small, fixed
set of dorks and asks a search engine for each: paste sites, exposed
documents, code hosts, leak chatter, the surface web at large.

Providers, first match wins:

  1. google  - the real Google index, through an official API:
       SERPER_API_KEY (serper.dev), or
       GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID (Programmable Search JSON API).
     Google has no keyless endpoint and blocks scraped result pages, so
     Google dorking needs one of these keys.
  2. brave   - BRAVE_API_KEY (official search API).
  3. keyless - Bing HTML, DuckDuckGo HTML and Mojeek, rotated per query.
     They understand the same operators but rate-limit quickly; a blocked
     engine is recorded as an error for that query, never as "no results".

Evidence law: a search hit is NOT a confirmed pivot. The engine indexed the
page at some point; nothing proves the page still contains the identifier.
Every hit is therefore a LEAF (category "web-mention"): listed in the
report, never expanded into new leads by the engine. The exact-document
module is the one that fetches a page and confirms the identifier is in it.
"""

from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

import httpx

from thousand_eyes.http_client import describe_http_error
from thousand_eyes.modules import exact_document

MODULE = "web-dorks"

MAX_QUERIES = 6        # dorks per lead
HITS_PER_QUERY = 5     # listed results per dork

_PASTE_SITES = ("pastebin.com", "rentry.co", "justpaste.it", "ghostbin.com",
                "controlc.com")
_DOC_TYPES = ("pdf", "xls", "xlsx", "csv", "txt", "doc", "docx")


class ProviderError(Exception):
    """The search engine answered, but not with results (block page, bad key)."""


def _any_site(sites) -> str:
    return " OR ".join(f"site:{s}" for s in sites)


def _any_filetype(types) -> str:
    return " OR ".join(f"filetype:{t}" for t in types)


def queries(kind: str, value: str) -> list[str]:
    """The dork set for one lead. Deterministic: same lead, same queries."""
    v = value.strip()
    q = f'"{v}"'
    if kind == "email":
        return [
            q,
            f"{q} {_any_site(_PASTE_SITES)}",
            f"{q} {_any_filetype(_DOC_TYPES)}",
            f"{q} site:github.com OR site:gitlab.com OR site:bitbucket.org",
            f'{q} "password" OR "leak" OR "breach" OR "dump"',
            f"{q} -site:linkedin.com -site:github.com -site:facebook.com",
        ]
    if kind == "username":
        return [
            q,
            f"{q} inurl:profile OR inurl:user OR inurl:users OR inurl:member",
            f"{q} {_any_site(_PASTE_SITES)}",
            f"{q} site:github.com OR site:reddit.com OR site:stackoverflow.com",
            f"{q} site:t.me OR site:discord.com OR site:keybase.io",
            f"{q} {_any_filetype(_DOC_TYPES)}",
        ]
    if kind == "domain":
        return [
            f"site:{v}",
            f"site:{v} {_any_filetype(_DOC_TYPES)}",
            f"site:{v} ext:env OR ext:sql OR ext:log OR ext:bak OR ext:conf",
            f'"@{v}"',
            f"{q} {_any_site(_PASTE_SITES)}",
            f"{q} -site:{v}",
        ]
    if kind == "phone":
        return [
            q,
            f"{q} {_any_site(_PASTE_SITES)}",
            f"{q} {_any_filetype(_DOC_TYPES)}",
        ]
    if kind == "url":
        host = urlparse(v).hostname or ""
        out = [q]
        if host:
            out.append(f"{q} -site:{host}")
        return out
    return [q]


# ------------------------------------------------------------ providers
# every provider: (client, query) -> list of {"url", "title", "snippet"}

def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


async def serper(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.post(
        exact_document.SERPER_URL,
        headers={"X-API-KEY": os.environ.get("SERPER_API_KEY", ""),
                 "Content-Type": "application/json"},
        json={"q": query, "num": 10},
    )
    if resp.status_code != 200:
        raise ProviderError(f"unexpected status {resp.status_code}")
    return [{"url": i.get("link", ""), "title": i.get("title", ""),
             "snippet": i.get("snippet", "")}
            for i in (resp.json().get("organic") or [])]


async def google_cse(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        exact_document.GOOGLE_CSE_URL,
        params={"key": os.environ.get("GOOGLE_CSE_API_KEY", ""),
                "cx": os.environ.get("GOOGLE_CSE_ID", ""), "q": query, "num": 10},
    )
    if resp.status_code != 200:
        raise ProviderError(f"unexpected status {resp.status_code}")
    return [{"url": i.get("link", ""), "title": i.get("title", ""),
             "snippet": i.get("snippet", "")}
            for i in (resp.json().get("items") or [])]


async def brave(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        exact_document.BRAVE_URL,
        headers={"X-Subscription-Token": os.environ.get("BRAVE_API_KEY", ""),
                 "Accept": "application/json"},
        params={"q": query, "count": 10},
    )
    if resp.status_code != 200:
        raise ProviderError(f"unexpected status {resp.status_code}")
    results = (resp.json().get("web") or {}).get("results") or []
    return [{"url": i.get("url", ""), "title": i.get("title", ""),
             "snippet": _strip(i.get("description", ""))} for i in results]


async def bing(client: httpx.AsyncClient, query: str) -> list[dict]:
    urls, err = await exact_document._bing_html_search(client, query)
    if err:
        raise ProviderError(err.split(": ", 1)[-1])
    return [{"url": u, "title": "", "snippet": ""} for u in urls]


async def ddg(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
    if resp.status_code != 200:
        # 202 is DuckDuckGo's bot challenge: blocked, not "no results"
        raise ProviderError(f"unexpected status {resp.status_code}")
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*'
                         r'(?:<a[^>]*class="result__snippet"[^>]*>(.*?)</a>)?',
                         resp.text, re.S):
        url, title, snip = m.group(1), m.group(2), (m.group(3) or "")
        um = re.search(r"uddg=([^&]+)", url)
        if um:
            url = unquote(um.group(1))
        out.append({"url": url, "title": _strip(title)[:120],
                    "snippet": _strip(snip)[:220]})
    return out


async def mojeek(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get("https://www.mojeek.com/search", params={"q": query})
    if resp.status_code != 200:
        raise ProviderError(f"unexpected status {resp.status_code}")
    out = []
    for m in re.finditer(r'<a class="title[^"]*" href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?'
                         r'(?:<p class="s">(.*?)</p>)?', resp.text, re.S):
        out.append({"url": m.group(1), "title": _strip(m.group(2))[:120],
                    "snippet": _strip(m.group(3) or "")[:220]})
    return out


def google_provider():
    """(name, fn) for the Google index, or None when no Google key is set."""
    if os.environ.get("SERPER_API_KEY"):
        return ("google", serper)
    if os.environ.get("GOOGLE_CSE_API_KEY") and os.environ.get("GOOGLE_CSE_ID"):
        return ("google", google_cse)
    return None


def providers() -> list[tuple[str, object]]:
    """Search providers for this run, in rotation order."""
    google = google_provider()
    if google:
        return [google]
    if os.environ.get("BRAVE_API_KEY"):
        return [("brave", brave)]
    return [("bing", bing), ("duckduckgo", ddg), ("mojeek", mojeek)]


# ------------------------------------------------------------------ run

def _label(engine: str, hit: dict) -> str:
    url = hit["url"]
    shown = url if len(url) <= 96 else url[:95] + "…"
    title = hit.get("title") or ""
    return f"web mention ({engine}): {shown}" + (f" - {title[:80]}" if title else "")


async def run(client: httpx.AsyncClient, value: str, kind: str = "email") -> dict:
    """Returns {"pivots": [], "leaves": [...], "errors": [...], "checked": int,
    "search_source": str}. `checked` = dorks a search engine answered."""
    engines = providers()
    leaves: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    checked = 0

    for i, query in enumerate(queries(kind, value)[:MAX_QUERIES]):
        name, fn = engines[i % len(engines)]
        try:
            hits = await fn(client, query)
        except httpx.HTTPError as exc:
            errors.append(f"{MODULE}:{name}: {describe_http_error(exc)} [{query}]")
            continue
        except (ProviderError, ValueError) as exc:
            errors.append(f"{MODULE}:{name}: {exc} [{query}]")
            continue
        checked += 1
        kept = 0
        for hit in hits:
            url = (hit.get("url") or "").strip()
            if not url.startswith("http") or url.lower() in seen:
                continue
            seen.add(url.lower())
            leaves.append({
                "site": _label(name, hit),
                "url": url,
                "status": None,
                "category": "web-mention",
                "query": query,
                "engine": name,
                "snippet": hit.get("snippet") or hit.get("title") or "",
            })
            kept += 1
            if kept >= HITS_PER_QUERY:
                break

    return {"pivots": [], "leaves": leaves, "errors": errors, "checked": checked,
            "search_source": ",".join(dict.fromkeys(n for n, _ in engines))}

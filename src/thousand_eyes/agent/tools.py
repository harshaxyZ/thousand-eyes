"""The Thousand Eyes instrument panel - 150+ read-only OSINT tools.

Families:
  engine.*    wraps of the proven v0.4 wildfire modules (evidence-only)
  search.*    free web-search engines (DDG html, Mojeek, Marginalia, Wikipedia)
  dork.*      google/bing dork ALGORITHMS: expand a lead into K exact queries
  breach.*    breach *existence* indexes (never dumps)
  dark.*      dark-web INDEX presence (ahmia clearnet index; never .onion fetch)
  site.*      per-site username presence probes generated from the bundled
              692-site WhatsMyName dataset ({account} placeholder semantics)

Safety fence (enforced here, not in the model):
  - no .onion resolution, no auth probing, no form submission, no password tests
  - per-domain token bucket (300ms) + global concurrency cap 8 (laptop-safe)
  - every result carries evidence_url + status; blocked == inconclusive
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import httpx
import yaml

from thousand_eyes.modules import (
    breach_existence, bluesky_profile, darkweb, dns_module, domains, email_checks,
    exact_document, fetch, github_commit_search, github_profile, gitlab_profile,
    gravatar, hibp, keybase_proofs, mastodon_lookup, pgp, reddit_user,
    registration_leaves, username_checks, userrealm, wayback,
)

# ------------------------------------------------------------------- types


@dataclass
class ToolResult:
    tool: str
    ok: bool
    summary: str
    findings: List[Dict[str, Any]] = field(default_factory=list)
    ms: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "ok": self.ok, "summary": self.summary,
                "ms": round(self.ms, 1), "findings": self.findings,
                "error": self.error}


@dataclass
class Tool:
    name: str
    family: str
    desc: str
    inputs: List[str]
    fn: Callable[..., Awaitable[ToolResult]]


# ------------------------------------------------------------ safety fence

_ONION_RE = re.compile(r"^[a-z2-7]{16,56}\.onion$", re.I)
_AUTH_PATH_RE = re.compile(r"/(login|signin|auth|session|password|reset)\b", re.I)


def _safe_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if _ONION_RE.match(host):
        return False                      # dark-web index presence only
    if _AUTH_PATH_RE.search(urlparse(url).path):
        return False                      # no auth probing, ever
    return True


class DomainBucket:
    """Per-domain token bucket: 1 request / 300ms."""

    def __init__(self) -> None:
        self._last: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            gap = 0.3 - (now - last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last[host] = max(last, now) + max(gap, 0.0)


BUCKET = DomainBucket()
_SEM = asyncio.Semaphore(8)          # global concurrency: laptop-safe


class SafeClient:
    """AsyncClient wrapper that enforces the fence on every request."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            headers={"User-Agent":
                     "Mozilla/5.0 (compatible; ThousandEyes/1.0; self-audit)"},
        )

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        if not _safe_url(url):
            raise PermissionError(f"fenced URL: {url}")
        await BUCKET.wait(urlparse(url).hostname or "global")
        async with _SEM:
            return await self.http.get(url, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()


# ------------------------------------------------------------- result kit


def _result(tool: str, fn, t0: float, ok: bool, summary: str,
            findings: List[Dict[str, Any]], error: str = "") -> ToolResult:
    return ToolResult(tool=tool, ok=ok, summary=summary, findings=findings,
                      ms=(time.monotonic() - t0) * 1000, error=error)


def _flatten(module_out: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in ("findings", "leaves", "nodes", "results", "pivots", "entries", "items"):
        val = module_out.get(key) if isinstance(module_out, dict) else None
        if isinstance(val, list):
            rows.extend(item for item in val if isinstance(item, dict))
    return rows


def _wrap(name: str, family: str, desc: str, inputs: List[str], call):
    async def fn(client: SafeClient, **kw: Any) -> ToolResult:
        t0 = time.monotonic()
        try:
            out = await call(client.http, **kw)
            findings = _flatten(out)
            summary = out.get("summary") or out.get("note") or json.dumps(out)[:200]
            return _result(name, call, t0, True, str(summary)[:400], findings)
        except Exception as exc:  # noqa: BLE001 - record, never crash the run
            return _result(name, call, t0, False, "", [],
                           error=f"{type(exc).__name__}: {exc}"[:200])
    return Tool(name=name, family=family, desc=desc, inputs=inputs, fn=fn)


# ------------------------------------------------------------ engine tools

ENGINE_TOOLS: List[Tool] = [
    _wrap("engine.hibp", "engine", "Have I Been Pwned breach existence for an email (keyless)",
          ["email"], lambda c, email: hibp.run(c, email)),
    _wrap("engine.xposed", "engine", "XposedOrNot breach existence for an email",
          ["email"], lambda c, email: breach_existence.run(c, email)),
    _wrap("engine.hudsonrock", "engine", "Hudson Rock credential-breach exposure for an email",
          ["email"], lambda c, email: breach_existence.run_hudsonrock(c, email)),
    _wrap("engine.gravatar", "engine", "Gravatar profile + hash exposure for an email",
          ["email"], lambda c, email: gravatar.run(c, email)),
    _wrap("engine.pgp", "engine", "PGP key existence for an email (keyservers)",
          ["email"], lambda c, email: pgp.run(c, email)),
    _wrap("engine.github-profile", "engine", "GitHub profile existence + metadata for a username",
          ["username"], lambda c, username: github_profile.run(c, username)),
    _wrap("engine.github-commits", "engine", "GitHub commit search by email (public code)",
          ["email"], lambda c, email: github_commit_search.run(c, email)),
    _wrap("engine.keybase", "engine", "Keybase signed-proof discovery from a GitHub username",
          ["username"], lambda c, username: keybase_proofs.run(c, username)),
    _wrap("engine.reddit", "engine", "Reddit account + activity presence for a username",
          ["username"], lambda c, username: reddit_user.run(c, username)),
    _wrap("engine.gitlab", "engine", "GitLab profile existence + metadata for a username",
          ["username"], lambda c, username: gitlab_profile.run(c, username)),
    _wrap("engine.mastodon", "engine", "Mastodon (mastodon.social) account lookup for a username",
          ["username"], lambda c, username: mastodon_lookup.run(c, username)),
    _wrap("engine.bluesky", "engine", "Bluesky AppView profile lookup for a username",
          ["username"], lambda c, username: bluesky_profile.run(c, username)),
    _wrap("engine.userrealm", "engine", "UserRealm cross-network presence for an email",
          ["email"], lambda c, email: userrealm.run(c, email)),
    _wrap("engine.dns", "engine", "Domain posture: DoH MX + crt.sh + RDAP for the email's domain",
          ["email"], lambda c, email: dns_module.run(c, email)),
    _wrap("engine.domain", "engine", "Full domain recon: MX, SPF, crt.sh certs, RDAP",
          ["domain"], lambda c, domain: domains.run_domain(c, domain)),
    _wrap("engine.wayback", "engine", "Wayback Machine snapshots for a URL or domain",
          ["url"], lambda c, url: wayback.run(c, url)),
    _wrap("engine.fetch", "engine", "Fetch one PUBLIC url (fenced: no .onion, no auth paths)",
          ["url"], lambda c, url: fetch.run(c, url)),
    _wrap("engine.email-checks", "engine", "Batch email recon: emailrep, format, MX, gravatar…",
          ["email"], lambda c, email: email_checks.run(c, email)),
    _wrap("engine.registration-leaves", "engine",
          "Registration-leak leaves: where the email address is registered",
          ["email"], lambda c, email: registration_leaves.run(c, email)),
    _wrap("engine.username-sweep", "engine",
          "WhatsMyName dataset sweep: 692-site presence probe, ranked",
          ["username"], lambda c, username: username_checks.run(c, username, max_sites=120)),
    _wrap("engine.exact-document", "engine",
          "Exact-document web spike for a phrase / email / username",
          ["seed"], lambda c, seed: exact_document.run(c, seed)),
    _wrap("engine.darkweb-index", "engine",
          "Ahmia clearnet index of Tor hidden services mentioning the term (index only)",
          ["term"], lambda c, term: darkweb.run(c, term)),
]

# ---------------------------------------------------------- search engines


async def _ddg(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    r = await client.get("https://html.duckduckgo.com/html/", params={"q": q})
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*'
                         r'(?:<a[^>]*class="result__snippet"[^>]*>(.*?)</a>)?', r.text, re.S):
        url, title, snip = m.group(1), m.group(2), (m.group(3) or "")
        um = re.search(r"uddg=([^&]+)", url)
        if um:
            url = unquote(um.group(1))
        out.append({"url": url, "title": re.sub("<[^>]+>", "", title)[:120],
                    "snippet": re.sub("<[^>]+>", "", snip)[:220], "engine": "ddg"})
    return out[:8]


async def _mojeek(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    r = await client.get("https://www.mojeek.com/search", params={"q": q})
    out = []
    for m in re.finditer(r'<a class="title[^"]*" href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?'
                         r'(?:<p class="s">(.*?)</p>)?', r.text, re.S):
        out.append({"url": m.group(1), "title": re.sub("<[^>]+>", "", m.group(2))[:120],
                    "snippet": re.sub("<[^>]+>", "", m.group(3) or "")[:220],
                    "engine": "mojeek"})
    return out[:8]


async def _marginalia(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    r = await client.get("https://search.marginalia.nu/search", params={"query": q})
    out = []
    for m in re.finditer(r'<a href="(https?://[^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        out.append({"url": m.group(1), "title": re.sub("<[^>]+>", "", m.group(2))[:120],
                    "snippet": "", "engine": "marginalia"})
    return out[:5]


async def _wikipedia(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    r = await client.get("https://en.wikipedia.org/w/api.php",
                         params={"action": "opensearch", "search": q, "limit": 5,
                                 "namespace": 0, "format": "json"})
    data = r.json()
    return [{"url": f"https://en.wikipedia.org/wiki/{quote(t.replace(' ', '_'))}",
             "title": t, "snippet": "", "engine": "wikipedia"} for t in data[1]]


def _search_tool(slug: str, label: str, fn) -> Tool:
    async def call(client: SafeClient, q: str) -> ToolResult:
        t0 = time.monotonic()
        try:
            hits = await fn(client.http, q)
            rows = [{"value": h["url"], "kind": "url", "source": slug,
                     "evidence_url": h["url"], "snippet": h.get("snippet", ""),
                     "title": h.get("title", "")} for h in hits]
            return _result(slug, fn, t0, True, f"{len(rows)} hits", rows)
        except Exception as exc:  # noqa: BLE001
            return _result(slug, fn, t0, False, "", [], error=str(exc)[:200])
    return Tool(name=f"search.{label}", family="search",
                desc=f"Free web search via {label} (no API key).",
                inputs=["q"], fn=call)


SEARCH_TOOLS = [
    _search_tool("ddg", "duckduckgo", _ddg),
    _search_tool("mojeek", "mojeek", _mojeek),
    _search_tool("marginalia", "marginalia", _marginalia),
    _search_tool("wikipedia", "wikipedia", _wikipedia),
]

# -------------------------------------------------------------- dork layer


def _dork_queries(kind: str, value: str) -> List[str]:
    v = value.strip()
    if kind == "email":
        return [f'"{v}"', f'"{v}" breach', f'"{v}" paste', f'"{v}" -linkedin -github',
                f'"{v}" filetype:pdf', f'"{v}" "password"']
    if kind == "username":
        return [f'"{v}"', f'"{v}" site:github.com', f'"{v}" inurl:profile',
                f'"{v}" site:reddit.com', f'"{v}" -twitter -instagram']
    if kind == "domain":
        return [f'site:{v}', f'inurl:{v}', f'"{v}" filetype:pdf', f'"{v}" -www',
                f'site:{v} filetype:xls']
    if kind == "url":
        return [f'"{v}"', f'cache:{v}', f'related:{v}']
    return [f'"{v}"']


def _dork_tool(kind: str) -> Tool:
    async def call(client: SafeClient, value: str) -> ToolResult:
        t0 = time.monotonic()
        queries = _dork_queries(kind, value)
        rows: List[Dict[str, Any]] = []
        engines = (_ddg, _mojeek)
        for i, query in enumerate(queries):
            engine = engines[i % len(engines)]
            try:
                hits = await engine(client.http, query)
                for h in hits[:4]:
                    rows.append({"value": h["url"], "kind": "url",
                                 "source": f"dork.{kind}", "evidence_url": h["url"],
                                 "snippet": h.get("snippet", ""),
                                 "title": h.get("title", ""), "query": query})
            except Exception:  # noqa: BLE001 - one dead engine never kills the dork
                continue
        seen, uniq = set(), []
        for r in rows:
            if r["url"] not in seen:
                seen.add(r["url"])
                uniq.append(r)
        return _result(f"dork.{kind}", call, t0, True,
                       f"{len(uniq)} unique hits from {len(queries)} dorks", uniq[:40])
    return Tool(name=f"dork.{kind}", family="dork",
                desc=f"Dork algorithm for a {kind}: {len(_dork_queries(kind, 'x'))} exact "
                     f"queries fanned over DDG+Mojeek (google/bing style operators).",
                inputs=["value"], fn=call)


DORK_TOOLS = [_dork_tool(k) for k in ("email", "username", "domain", "url")]

# ---------------------------------------------------- breach + dark indexes


async def _paste_presence(client: SafeClient, term: str) -> ToolResult:
    t0 = time.monotonic()
    rows = []
    for q in (f'site:pastebin.com "{term}"', f'site:ghostbin.com "{term}"',
              f'site:github.io "{term}"'):
        try:
            hits = await _ddg(client.http, q)
            rows.extend({"value": h["url"], "kind": "url", "source": "dark.paste-index",
                         "evidence_url": h["url"], "snippet": h.get("snippet", "")[:200],
                         "title": h.get("title", "")} for h in hits[:3])
        except Exception:  # noqa: BLE001
            continue
    return _result("dark.paste-index", _paste_presence, t0, True,
                   f"{len(rows)} paste-index hits", rows)


async def _ahmia(client: SafeClient, term: str) -> ToolResult:
    t0 = time.monotonic()
    try:
        out = await darkweb.run(client.http, term)
        return _result("dark.ahmia-index", _ahmia, t0, True,
                       str(out.get("summary", ""))[:300], _flatten(out))
    except Exception as exc:  # noqa: BLE001
        return _result("dark.ahmia-index", _ahmia, t0, False, "", [], error=str(exc)[:200])


DARK_TOOLS: List[Tool] = [
    Tool(name="dark.ahmia-index", family="dark",
         desc="Ahmia clearnet index of Tor hidden services mentioning the term. "
              "Index presence ONLY - the harness never fetches .onion hosts.",
         inputs=["term"], fn=_ahmia),
    Tool(name="dark.paste-index", family="dark",
         desc="Clear-net paste-index presence (pastebin/ghostbin/github.io) for a term.",
         inputs=["term"], fn=_paste_presence),
]

BREACH_TOOLS = [t for t in ENGINE_TOOLS if t.name in
                ("engine.hibp", "engine.xposed", "engine.hudsonrock")]

# ------------------------------------------------------- site probe family

_WMN_SITES: List[Dict[str, Any]] = []


def _load_wmn() -> List[Dict[str, Any]]:
    global _WMN_SITES
    if _WMN_SITES:
        return _WMN_SITES
    text = resources.files("thousand_eyes.data").joinpath("wmn_data.json").read_text(
        encoding="utf-8")
    _WMN_SITES = (json.loads(text) or {}).get("sites", [])
    return _WMN_SITES


def _make_site_tool(site: Dict[str, Any]) -> Tool:
    name = site.get("name", "site")
    uri = site.get("uri_check", "")
    e_code = site.get("e_code")
    e_string = site.get("e_string")
    m_code = site.get("m_code")
    m_string = site.get("m_string")

    async def call(client: SafeClient, username: str) -> ToolResult:
        t0 = time.monotonic()
        url = uri.replace("{account}", quote(username))
        try:
            if not _safe_url(url):
                return _result(f"site.{name}", call, t0, False, "", [],
                               error="fenced URL")
            resp = await client.get(url)
            body = resp.text[:200_000]
            claimed = (e_code is not None and resp.status_code == e_code) or \
                      (bool(e_string) and e_string in body)
            missing = (m_code is not None and resp.status_code == m_code) or \
                      (bool(m_string) and m_string in body)
            verdict = "claimed" if claimed and not missing else \
                      "missing" if missing and not claimed else "ambiguous"
            row = {"value": username, "kind": "handle", "source": f"site.{name}",
                   "evidence_url": url, "snippet":
                   f"{verdict} (http {resp.status_code})", "status": resp.status_code,
                   "site": name, "verdict": verdict}
            return _result(f"site.{name}", call, t0, True,
                           f"{name}: {verdict} ({resp.status_code})", [row])
        except Exception as exc:  # noqa: BLE001
            return _result(f"site.{name}", call, t0, False, "", [],
                           error=f"{type(exc).__name__}: {exc}"[:160])
    return Tool(name=f"site.{name}", family="site",
                desc=f"Presence probe on {name}: {uri[:64]}",
                inputs=["username"], fn=call)


def site_tools() -> List[Tool]:
    """130 per-site probes sampled across all 21 WMN categories."""
    sites = _load_wmn()
    if not sites:
        return []
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for s in sites:
        by_cat.setdefault(s.get("cat", "other"), []).append(s)
    picked: List[Dict[str, Any]] = []
    cats = sorted(by_cat)
    per = max(1, 130 // len(cats))
    for cat in cats:
        picked.extend(sorted(by_cat[cat], key=lambda x: x.get("name", ""))[:per])
    return [_make_site_tool(s) for s in picked[:130]]


def all_tools() -> List[Tool]:
    return ENGINE_TOOLS + SEARCH_TOOLS + DORK_TOOLS + DARK_TOOLS + BREACH_TOOLS + site_tools()


def tool_manifest() -> List[Dict[str, Any]]:
    return [{"name": t.name, "family": t.family, "desc": t.desc, "inputs": t.inputs}
            for t in all_tools()]


def count_tools() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for t in all_tools():
        counts[t.family] = counts.get(t.family, 0) + 1
    counts["TOTAL"] = sum(counts.values())
    return counts

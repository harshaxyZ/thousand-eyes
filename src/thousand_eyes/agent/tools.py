"""The Thousand Eyes instrument panel - 160+ read-only OSINT tools.

Families:
  engine.*    the registry modules (evidence-only), one tool per source
  dork.*      the registry's web-dorks module, one tool per lead type:
              a lead expanded into exact queries with search operators
              (Google through an API key, else Bing/DDG/Mojeek)
  search.*    one free-text query on one engine (Google, Bing, DDG, Mojeek,
              Marginalia, Wikipedia)
  dark.*      dark-web INDEX presence (ahmia clearnet index; never .onion fetch)
  site.*      per-site username presence probes generated from the bundled
              692-site WhatsMyName dataset ({account} placeholder semantics)

Safety fence (enforced here, not in the model):
  - no .onion resolution, no auth probing, no form submission, no password tests
  - per-domain token bucket (300ms) + connection cap 8 (laptop-safe)
  - every result carries evidence_url + status; blocked == inconclusive

The fence is a request hook on the shared httpx client, so it covers every
outbound request of every tool family (and every redirect hop), not only the
calls that go through SafeClient.get().
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Awaitable, Callable, Dict, List
from urllib.parse import quote, unquote, urlparse

import httpx

from thousand_eyes import __version__, registry
from thousand_eyes.modules import darkweb, dorks

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
    #: lead kinds this tool can take (empty = any kind)
    accepts: tuple = ()
    #: only fired for leads at/below this round (None = every round)
    max_depth: int | None = None
    #: built from a registry ModuleSpec (understands the _all_sites option)
    from_registry: bool = False

    def takes(self, kind: str) -> bool:
        return not self.accepts or kind in self.accepts


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


class FencedURL(httpx.RequestError):
    """A request the fence refused. It is an httpx error on purpose: modules
    already treat transport errors as INCONCLUSIVE, so a fenced URL can never
    crash a sweep or be laundered into a negative."""


class DomainBucket:
    """Per-domain token bucket: 1 request / 300ms."""

    GAP = 0.3

    def __init__(self) -> None:
        self._next: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        # reserve the slot under the lock, sleep outside it - one slow host
        # must not stall requests to every other host
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + self.GAP
        if slot > now:
            await asyncio.sleep(slot - now)


BUCKET = DomainBucket()


async def _fence_hook(request: httpx.Request) -> None:
    """Runs before EVERY outbound request of the agent's http client."""
    url = str(request.url)
    if not _safe_url(url):
        raise FencedURL(f"fenced URL: {url}", request=request)
    await BUCKET.wait(request.url.host or "global")


class SafeClient:
    """AsyncClient wrapper that enforces the fence on every request.

    `http` is the underlying httpx client that engine modules, search and
    dork tools use directly; the fence hook is installed on it, so there is
    no path around it. `transport` is for tests and --demo (fixture replay).
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            headers={"User-Agent":
                     f"Mozilla/5.0 (compatible; thousand-eyes/{__version__}; self-audit)"},
            follow_redirects=True,     # same as the engine; each hop is fenced
            event_hooks={"request": [_fence_hook]},
            transport=transport,
        )

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        if not _safe_url(url):
            raise PermissionError(f"fenced URL: {url}")
        return await self.http.get(url, **kw)

    async def aclose(self) -> None:
        await self.http.aclose()


# ------------------------------------------------------------- result kit


def _result(tool: str, fn, t0: float, ok: bool, summary: str,
            findings: List[Dict[str, Any]], error: str = "") -> ToolResult:
    return ToolResult(tool=tool, ok=ok, summary=summary, findings=findings,
                      ms=(time.monotonic() - t0) * 1000, error=error)


def _flatten(module_out: Dict[str, Any], tool: str = "") -> List[Dict[str, Any]]:
    """Module output -> evidence rows the ledger can dedup and cite.

    Modules return pivots ({type, value, source, evidence_url, snippet}) and
    leaves ({site, url, status, category}). A leaf has no kind/value of its
    own, so without this mapping every leaf of a run collapsed into a single
    ledger row.
    """
    rows: List[Dict[str, Any]] = []
    if not isinstance(module_out, dict):
        return rows
    for p in module_out.get("pivots") or []:
        if isinstance(p, dict):
            rows.append({"kind": p.get("type", ""), **p})
    for leaf in module_out.get("leaves") or []:
        if isinstance(leaf, dict):
            rows.append({
                "kind": leaf.get("category") or "leaf",
                "value": leaf.get("site", ""),
                "source": tool or leaf.get("category", ""),
                "evidence_url": leaf.get("url", ""),
                "snippet": leaf.get("snippet") or leaf.get("site", ""),
                "status": leaf.get("status"),
            })
    for key in ("findings", "nodes", "results", "entries", "items"):
        val = module_out.get(key)
        if isinstance(val, list):
            rows.extend(item for item in val if isinstance(item, dict))
    return rows


def _wrap(name: str, family: str, desc: str, inputs: List[str], call,
          accepts: tuple = ()):
    async def fn(client: SafeClient, **kw: Any) -> ToolResult:
        t0 = time.monotonic()
        try:
            out = await call(client.http, **kw)
            findings = _flatten(out, name)
            summary = out.get("summary") or out.get("note") or (
                f"{len(findings)} finding(s), "
                f"{len(out.get('errors') or [])} sub-check(s) did not answer")
            # a module that reports a hard error did not answer: inconclusive
            hard = out.get("error")
            return _result(name, call, t0, not hard, str(summary)[:400], findings,
                           error=str(hard or "")[:200])
        except Exception as exc:  # noqa: BLE001 - record, never crash the run
            return _result(name, call, t0, False, "", [],
                           error=f"{type(exc).__name__}: {exc}"[:200])
    return Tool(name=name, family=family, desc=desc, inputs=inputs, fn=fn,
                accepts=accepts)


# ------------------------------------------------------------ engine tools

def _from_spec(spec) -> Tool:
    """Build an agent tool straight from the registry - one source of truth.

    The deterministic engine and the LLM tool layer therefore always expose
    exactly the same sources: add a module to registry.py and both paths
    gain it, with the same fence and the same evidence rules.
    """
    from thousand_eyes.registry import Ctx

    arg = spec.inputs[0] if spec.inputs else "seed"

    async def call(client, **kw):
        all_sites = bool(kw.pop("_all_sites", False))
        value = ""
        for candidate in kw.values():
            if isinstance(candidate, str) and candidate:
                value = candidate
                break
        if not value:
            raise ValueError(f"{spec.tool_name}: no value supplied")
        return await spec.fn(client, value, Ctx(all_sites=all_sites))

    # the family is the name prefix: engine.github-profile, dork.email, ...
    tool = _wrap(spec.tool_name, spec.tool_name.split(".")[0],
                 spec.desc or spec.label, [arg], call,
                 accepts=tuple(spec.lead_types))
    tool.max_depth = spec.max_depth
    tool.from_registry = True
    return tool


ENGINE_TOOLS: List[Tool] = [
    _from_spec(spec) for spec in registry.MODULES
]

# ---------------------------------------------------------- search engines


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


async def _google(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    provider = dorks.google_provider()
    if provider is None:
        raise RuntimeError("no Google key: set SERPER_API_KEY, or "
                           "GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID")
    return (await provider[1](client, q))[:8]


async def _ddg(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    return (await dorks.ddg(client, q))[:8]


async def _mojeek(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    return (await dorks.mojeek(client, q))[:8]


async def _bing(client: httpx.AsyncClient, q: str) -> List[Dict[str, str]]:
    return (await dorks.bing(client, q))[:8]


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
                desc=f"One free-text web search via {label}"
                     + (" (needs SERPER_API_KEY or GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID)."
                        if label == "google" else " (no API key)."),
                inputs=["q"], fn=call)


SEARCH_TOOLS = [
    _search_tool("google", "google", _google),
    _search_tool("bing", "bing", _bing),
    _search_tool("ddg", "duckduckgo", _ddg),
    _search_tool("mojeek", "mojeek", _mojeek),
    _search_tool("marginalia", "marginalia", _marginalia),
    _search_tool("wikipedia", "wikipedia", _wikipedia),
]

# -------------------------------------------------------------- dork layer

#: dork.<kind> tools are registry modules (modules/dorks.py): the same dork
#: set runs in the default cascade, the TUI and the agent
DORK_TOOLS = [t for t in ENGINE_TOOLS if t.family == "dork"]

# ---------------------------------------------------- breach + dark indexes


async def _paste_presence(client: SafeClient, term: str) -> ToolResult:
    t0 = time.monotonic()
    rows = []
    answered = 0
    last_error = ""
    for q in (f'site:pastebin.com "{term}"', f'site:rentry.co "{term}"',
              f'site:justpaste.it "{term}"'):
        try:
            hits = await dorks.ddg(client.http, q)
        except Exception as exc:  # noqa: BLE001 - one blocked query never kills the tool
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            continue
        answered += 1
        rows.extend({"value": h["url"], "kind": "url", "source": "dark.paste-index",
                     "evidence_url": h["url"], "snippet": h.get("snippet", "")[:200],
                     "title": h.get("title", "")} for h in hits[:3])
    # no query answered = blocked = inconclusive, never "no paste mentions"
    return _result("dark.paste-index", _paste_presence, t0, answered > 0,
                   f"{len(rows)} paste-index hits from {answered}/3 queries", rows,
                   error="" if answered else last_error)


async def _ahmia(client: SafeClient, term: str) -> ToolResult:
    t0 = time.monotonic()
    try:
        out = await darkweb.run(client.http, term)
        return _result("dark.ahmia-index", _ahmia, t0, True,
                       str(out.get("note") or "")[:300],
                       _flatten(out, "dark.ahmia-index"))
    except Exception as exc:  # noqa: BLE001
        return _result("dark.ahmia-index", _ahmia, t0, False, "", [], error=str(exc)[:200])


DARK_TOOLS: List[Tool] = [
    Tool(name="dark.ahmia-index", family="dark",
         desc="Ahmia clearnet index of Tor hidden services mentioning the term. "
              "Index presence ONLY - the harness never fetches .onion hosts.",
         inputs=["term"], fn=_ahmia),
    Tool(name="dark.paste-index", family="dark",
         desc="Clear-net paste-index presence (pastebin/rentry/justpaste) for a term.",
         inputs=["term"], fn=_paste_presence),
]

#: breach *existence* indexes (never dumps): a view over ENGINE_TOOLS, not a
#: separate family, so they are not registered twice
BREACH_TOOL_NAMES = ("engine.hibp", "engine.xposed", "engine.hudsonrock",
                     "engine.leakcheck")
BREACH_TOOLS = [t for t in ENGINE_TOOLS if t.name in BREACH_TOOL_NAMES]

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
                inputs=["username"], fn=call, accepts=("username",))


def site_tools() -> List[Tool]:
    """Per-site probes sampled evenly across the WMN categories (max 130)."""
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
    # DORK_TOOLS and BREACH_TOOLS are views over ENGINE_TOOLS
    return ENGINE_TOOLS + SEARCH_TOOLS + DARK_TOOLS + site_tools()


def tool_manifest() -> List[Dict[str, Any]]:
    return [{"name": t.name, "family": t.family, "desc": t.desc, "inputs": t.inputs}
            for t in all_tools()]


def count_tools() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for t in all_tools():
        counts[t.family] = counts.get(t.family, 0) + 1
    counts["TOTAL"] = sum(counts.values())
    return counts

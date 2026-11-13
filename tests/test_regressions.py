"""Regression tests for the agent routing, the fence, per-lead batching,
concurrent module execution and the partial-failure accounting."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from thousand_eyes import registry
from thousand_eyes.agent import Planner, tools_for
from thousand_eyes.agent.tools import (
    FencedURL, SafeClient, Tool, ToolResult, all_tools,
)
from thousand_eyes.models import ScanResult
from thousand_eyes.output import _coverage_line
from thousand_eyes.scan import module_outcome, run_scan


class _SilentLLM:
    provider = "fake"
    model = "fake"

    async def chat(self, messages, timeout_s: float = 120.0):
        yield "# report", {}


# ------------------------------------------------------------- routing

def test_every_routed_tool_exists_and_takes_the_kind():
    """tools_for must never name a tool the registry does not have."""
    tools = {t.name: t for t in all_tools()}
    for kind in ("email", "username", "domain", "url", "phone", "name"):
        routed = tools_for(kind)
        assert routed, kind
        missing = [n for n in routed if n not in tools]
        assert not missing, f"{kind}: routed but not registered: {missing}"


def test_routing_is_generated_from_the_registry():
    """Every registry module that answers a lead type is fired for it."""
    for kind in registry.lead_types_covered():
        routed = set(tools_for(kind))
        for spec in registry.modules_for(kind):
            assert spec.tool_name in routed, (kind, spec.tool_name)


def test_tool_names_are_unique():
    names = [t.name for t in all_tools()]
    assert len(names) == len(set(names))


# --------------------------------------------------------------- fence

async def test_fence_covers_the_raw_http_client():
    """Engine/search/dork tools use client.http directly. The fence must
    still refuse onion hosts and auth paths there, as an httpx error."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    client = SafeClient(transport=httpx.MockTransport(handler))
    try:
        for bad in ("http://abcdefghijklmnop.onion/x",
                    "https://site.example/login",
                    "https://site.example/password/reset"):
            with pytest.raises(FencedURL):
                await client.http.get(bad)
        assert seen == []
        resp = await client.http.get("https://site.example/profile/x")
        assert resp.status_code == 200
        assert seen == ["https://site.example/profile/x"]
    finally:
        await client.aclose()


async def test_fence_covers_redirect_hops():
    """A public URL that redirects into an auth path is stopped at the hop."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://site.example/login"})
        return httpx.Response(200, text="should never be reached")

    client = SafeClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FencedURL):
            await client.http.get("https://site.example/start")
    finally:
        await client.aclose()


# ------------------------------------------------------ per-lead batching

async def test_planner_fires_tools_for_every_lead_in_the_round():
    """Two emails in one round: each one gets the tools, not only the first."""
    calls: list[tuple[str, str]] = []

    def fake(name: str) -> Tool:
        async def fn(client, **kw):
            calls.append((name, next(iter(kw.values()))))
            return ToolResult(tool=name, ok=True, summary="", findings=[])
        return Tool(name=name, family=name.split(".")[0], desc="", inputs=["value"], fn=fn)

    planner = Planner(_SilentLLM(), goal="t",
                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    planner.tools = {n: fake(n) for n in tools_for("email")}
    await planner.run(["a@example.org", "b@example.org"], fast=True)

    for name in tools_for("email"):
        assert (name, "a@example.org") in calls
        assert (name, "b@example.org") in calls
    assert len(calls) == len(set(calls)), "a tool ran twice on the same lead"


async def test_planner_tool_budget_is_a_real_cap():
    calls: list[str] = []

    async def fn(client, **kw):
        calls.append(next(iter(kw.values())))
        return ToolResult(tool="t", ok=True, summary="", findings=[])

    planner = Planner(_SilentLLM(), goal="t", tool_budget=3,
                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    planner.tools = {n: Tool(name=n, family="engine", desc="", inputs=["value"], fn=fn)
                     for n in tools_for("email")}
    await planner.run(["a@example.org", "b@example.org"], fast=True)
    assert len(calls) == 3


async def test_a_crashing_tool_does_not_kill_the_batch():
    async def boom(client, **kw):
        raise KeyError("url")

    async def fine(client, **kw):
        return ToolResult(tool="ok", ok=True, summary="", findings=[
            {"kind": "url", "value": "https://x.example/a", "source": "t",
             "evidence_url": "https://x.example/a"}])

    planner = Planner(_SilentLLM(), goal="t",
                      transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    names = tools_for("phone")
    planner.tools = {names[0]: Tool(name=names[0], family="engine", desc="",
                                    inputs=["value"], fn=boom)}
    planner.tools["extra"] = Tool(name="extra", family="engine", desc="",
                                  inputs=["value"], fn=fine)
    report = await planner.run(["+14155552671"], fast=True)
    assert report == "# report"
    assert planner.analytics.tool_errors == 1


# ------------------------------------------------- concurrent module runs

async def test_modules_of_one_lead_run_concurrently_in_registry_order(monkeypatch, transport):
    """Slow modules overlap in time, but the graph is built in registry
    order no matter which one answers first."""
    def module(label: str, delay: float) -> registry.ModuleSpec:
        async def fn(client, value, ctx):
            await asyncio.sleep(delay)
            return {"pivots": [{"type": "url", "value": f"https://{label}.example/{value}",
                                "source": label, "evidence_url": f"https://{label}.example/",
                                "snippet": label, "status": 200}],
                    "leaves": []}
        return registry.ModuleSpec(label, ("username",), fn)

    # the slowest module is first in the registry
    monkeypatch.setattr(registry, "MODULES",
                        [module("slow", 0.4), module("mid", 0.2), module("fast", 0.0)])
    t0 = time.monotonic()
    result = await run_scan("fixturehandle", transport=transport, max_depth=0)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.55, f"modules ran one after another ({elapsed:.2f}s)"
    urls = [n.value for n in result.nodes.values() if n.type == "url"]
    assert urls == ["https://slow.example/fixturehandle",
                    "https://mid.example/fixturehandle",
                    "https://fast.example/fixturehandle"]
    assert result.meta["stages"][0]["modules"] == 3


# ------------------------------------------------- partial-failure accounting

def test_module_outcome_tells_partial_from_failed():
    assert module_outcome({"pivots": [], "leaves": []}) == "ok"
    assert module_outcome({"skipped": "no key"}) == "skipped"
    assert module_outcome({"error": "boom"}) == "failed"
    # a sweep where one site timed out but the rest answered
    assert module_outcome({"pivots": [], "errors": [{"error": "timeout"}],
                           "checked": 119}) == "partial"
    # nothing answered at all
    assert module_outcome({"pivots": [], "errors": ["timeout"], "checked": 0}) == "failed"


async def test_one_dead_site_does_not_fail_the_whole_sweep(monkeypatch, transport):
    async def sweep(client, value, ctx):
        return {"pivots": [], "leaves": [], "checked": 119,
                "errors": [{"module": "username-sweep:Dead", "error": "request failed"}]}

    monkeypatch.setattr(registry, "MODULES",
                        [registry.ModuleSpec("username-sweep", ("username",), sweep)])
    result = await run_scan("fixturehandle", transport=transport, max_depth=0)

    assert result.meta["sources"]["username-sweep"]["partial"] == 1
    assert result.meta["sources"]["username-sweep"]["failed"] == 0
    assert result.meta["stages"][0]["failures"] == 0
    # the sub-error is still listed: partial never hides a failed check
    assert [e.module for e in result.errors] == ["username-sweep:Dead"]
    assert "source coverage: 1 answered (1 only partly) · 0 failed" in _coverage_line(result)


def test_coverage_line_without_partials_is_unchanged():
    r = ScanResult(seed="x", seed_type="username")
    r.meta["sources"] = {"a": {"ok": 2, "failed": 1, "skipped": 0}}
    assert "source coverage: 2 answered · 1 failed" in _coverage_line(r)


# ------------------------------------------------------ dorks (search engines)

def test_dork_sets_cover_paste_sites_and_documents():
    from thousand_eyes.modules import dorks

    email = dorks.queries("email", "fixture.subject@example.org")
    assert email[0] == '"fixture.subject@example.org"'
    assert any("site:pastebin.com" in q for q in email)
    assert any("filetype:pdf" in q for q in email)
    domain = dorks.queries("domain", "example.org")
    assert domain[0] == "site:example.org"
    assert all(len(dorks.queries(k, "x1")) <= dorks.MAX_QUERIES
               for k in ("email", "username", "domain", "phone", "url"))


async def test_dorks_use_google_when_a_key_is_set(monkeypatch):
    """With SERPER_API_KEY every dork goes to the Google index, and a hit is
    a LEAF (a search hit is never a confirmed pivot)."""
    from thousand_eyes.modules import dorks

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(200, json={"organic": [
            {"link": "https://paste.example/abc", "title": "a paste", "snippet": "s"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await dorks.run(client, "fixture.subject@example.org", "email")

    assert set(hosts) == {"google.serper.dev"}
    assert res["search_source"] == "google"
    assert res["pivots"] == []
    assert res["checked"] == len(dorks.queries("email", "x"))
    assert [leaf["url"] for leaf in res["leaves"]] == ["https://paste.example/abc"]
    assert res["leaves"][0]["category"] == "web-mention"


async def test_blocked_search_engine_is_an_error_not_a_clean_result():
    """Keyless engines that answer with a block page (DuckDuckGo's 202) are
    recorded per query; nothing is reported as 'no results'."""
    from thousand_eyes.modules import dorks

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.bing.com":
            return httpx.Response(200, text="<html>There are no results for x</html>")
        return httpx.Response(202, text="challenge")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await dorks.run(client, "fixturehandle", "username")

    assert res["leaves"] == []
    assert res["checked"] == 2              # the two bing queries answered
    assert len(res["errors"]) == 4          # ddg + mojeek, two queries each
    assert module_outcome(res) == "partial"


def test_dorks_are_registry_modules_for_every_lead_type():
    for kind in ("email", "username", "domain", "phone", "url"):
        assert f"dork.{kind}" in tools_for(kind)
        spec = [s for s in registry.modules_for(kind) if s.tool_name == f"dork.{kind}"][0]
        assert spec.max_depth == 0          # seeds only: engines rate-limit fast


def test_web_mentions_rank_as_ambient():
    from thousand_eyes import impact

    tier, _ = impact.leaf_tier("web mention (google): https://x.example/a", "web-mention")
    assert tier == impact.AMBIENT


# ------------------------------------------------------------- dark index

async def test_darkweb_reads_the_rotating_form_token(monkeypatch):
    from thousand_eyes.modules import darkweb

    monkeypatch.setattr(darkweb, "_token_cache", None)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text=(
                '<form action="/search/"><input type="hidden" name="ab12cd" '
                'value="34ef56"><input name="q"></form>'))
        return httpx.Response(200, text='<ol class="searchResults"></ol>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await darkweb.run(client, "fixturehandle")

    assert res["errors"] == []
    assert seen[-1] == "https://ahmia.fi/search/?q=fixturehandle&ab12cd=34ef56"
    monkeypatch.setattr(darkweb, "_token_cache", None)


# ---------------------------------------------------------- breach sources

async def test_leakcheck_lists_sources_never_records():
    from thousand_eyes.modules import leakcheck

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "success": True, "found": 3, "fields": ["password", "phone"],
            "sources": [{"name": "Old.example", "date": "2015-01"},
                        {"name": "New.example", "date": "2026-02"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await leakcheck.run(client, "fixture.subject@example.org")

    sites = [leaf["site"] for leaf in res["leaves"]]
    assert sites[0].startswith("breach summary: 3 leaked record(s) in 2 source(s)")
    assert "password, phone" in sites[0]
    assert sites[1:] == ["breach: New.example (2026-02)", "breach: Old.example (2015-01)"]
    assert res["pivots"] == [] and res["error"] is None


async def test_leakcheck_not_found_is_clean_and_rate_limit_is_an_error():
    from thousand_eyes.modules import leakcheck

    replies = [httpx.Response(200, json={"success": False, "error": "Not found"}),
               httpx.Response(429, text="slow down")]

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: replies.pop(0))) as client:
        clean = await leakcheck.run(client, "fixture.subject@example.org")
        limited = await leakcheck.run(client, "fixture.subject@example.org")

    assert clean == {"pivots": [], "leaves": [], "error": None}
    assert limited["error"] == "leakcheck: rate-limited (status 429)"


async def test_hudsonrock_is_keyless_and_reads_the_live_shape():
    from thousand_eyes.modules import breach_existence

    def handler(request: httpx.Request) -> httpx.Response:
        assert "apikey" not in request.headers
        return httpx.Response(200, json={"stealers": [{
            "computer_name": "FIXTURE-PC", "date_compromised": "2026-03-04T00:00:00.000Z",
            "total_user_services": 7}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await breach_existence.run_hudsonrock(client, "fixture.subject@example.org")

    assert res["error"] is None and not res.get("skipped")
    assert [leaf["site"] for leaf in res["leaves"]] == [
        "stealer log: FIXTURE-PC (compromised 2026-03-04) - 7 saved login(s) at risk"]


def test_agent_ledger_keeps_every_leaf():
    """Leaves used to collapse into one ledger row (no kind/value of their own)."""
    from thousand_eyes.agent.tools import _flatten

    rows = _flatten({"leaves": [
        {"site": "breach: A", "url": "https://x.example/1", "category": "breach"},
        {"site": "breach: B", "url": "https://x.example/1", "category": "breach"}]},
        "engine.leakcheck")
    planner = Planner(_SilentLLM(), goal="t")
    assert planner._ledger_add("engine.leakcheck", rows) == 2
    assert {r["value"] for r in planner.ledger} == {"breach: A", "breach: B"}

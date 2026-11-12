"""Module-level regression tests for hard rules not already covered by the
5 mandated acceptance tests: #4 (leaf vs pivot classes), #5 (depth max 2),
dns's free-mailbox skip, and hibp's error-vs-skip-vs-leaf distinction."""

from __future__ import annotations

import hashlib

import httpx

from thousand_eyes.modules import dns_module, hibp
from thousand_eyes.scan import run_scan

FIXTURE_EMAIL = "fixture.subject@example.org"
FREE_MAIL_EMAIL = "fixture.subject@gmail.com"


def _gravatar_digest(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


# hibp: a breach name with no username field is a LEAF (not re-expanded),
# a breach payload WITH a username field is a PIVOT.
async def test_hibp_breach_without_username_is_leaf_with_username_is_pivot(router, transport, monkeypatch):
    monkeypatch.setenv("HIBP_API_KEY", "fixture-key")
    router.add_host_path(
        "haveibeenpwned.com",
        f"/api/v3/breachedaccount/{FIXTURE_EMAIL}",
        httpx.Response(
            200,
            json=[
                {"Name": "FixtureBreachNoUsername", "BreachDate": "2020-01-01"},
                {"Name": "FixtureBreachWithUsername", "BreachDate": "2021-06-15", "Username": "fixturehandle"},
            ],
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await hibp.run(client, FIXTURE_EMAIL)

    assert res["error"] is None
    assert res["skipped"] is None
    assert len(res["leaves"]) == 1
    assert "FixtureBreachNoUsername" in res["leaves"][0]["site"]
    assert len(res["pivots"]) == 1
    assert res["pivots"][0]["type"] == "handle"
    assert res["pivots"][0]["value"] == "fixturehandle"


def test_hibp_skips_without_api_key():
    import asyncio

    async def _run():
        async with httpx.AsyncClient() as client:
            return await hibp.run(client, FIXTURE_EMAIL)

    res = asyncio.run(_run())
    assert res["skipped"] is not None
    assert res["pivots"] == []
    assert res["leaves"] == []


async def test_dns_skips_free_mailbox_domains(router, transport):
    async with httpx.AsyncClient(transport=transport) as client:
        res = await dns_module.run(client, FREE_MAIL_EMAIL)

    assert res["skipped"] is not None
    assert "gmail.com" in res["skipped"]
    assert res["hosts"] == []
    # No DNS/crt.sh request should have been made at all for a free mailbox.
    assert router.requested_urls == []


async def test_dns_runs_for_non_free_mailbox_domain(router, transport):
    router.add_host_path(
        "cloudflare-dns.com",
        "/dns-query",
        httpx.Response(200, json={"Answer": [{"type": 15, "data": "10 mail.fixture-corp.example"}]}),
    )
    router.add_host_path("crt.sh", "/", httpx.Response(200, json=[]))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await dns_module.run(client, "fixture.subject@fixture-corp.example")

    assert res["skipped"] is None
    assert any("mail.fixture-corp.example" in h["value"] for h in res["hosts"])
    assert all(h["type"] == "mail infrastructure" for h in res["hosts"])


# v0.3 depth semantics: max_depth bounds how far spawned leads are
# processed. max_depth=1 means: seed modules run, their URL pivots are
# fetched (depth-1 leads), but anything found inside those fetches is only
# recorded as a node - exactly the legacy "depth max 2" rule.
async def test_depth_is_capped_at_two(router, transport):
    digest = _gravatar_digest(FIXTURE_EMAIL)
    depth1_url = "https://social.example/fixtureperson"
    depth2_url = "https://deeper.example/should-not-be-fetched"

    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(200, json={"entry": [{"accounts": [{"shortname": "x", "url": depth1_url}]}]}),
    )
    router.add_host_path(
        "social.example",
        "/fixtureperson",
        httpx.Response(200, text=f"profile page linking to {depth2_url}"),
    )

    result = await run_scan(FIXTURE_EMAIL, transport=transport, max_depth=1)

    # depth2_url must appear as a node (found via fetching the depth-1 pivot)...
    assert any(n.value == depth2_url for n in result.nodes.values())
    # ...but must never itself have been fetched (no request to that host/path).
    assert not any(depth2_url in u for u in router.requested_urls)

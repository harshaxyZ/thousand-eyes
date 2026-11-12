"""Module-level regression tests for hard rules not already covered by the
5 mandated acceptance tests: #4 (leaf vs pivot classes), #5 (depth max 2),
dns's free-mailbox skip, and hibp's error-vs-skip-vs-leaf distinction."""

from __future__ import annotations

import hashlib

import httpx

from thousand_eyes.modules import (bluesky_profile, chess_profile, dns_module,
                                    dockerhub_profile, gitlab_profile, hibp,
                                    hackernews_user, mastodon_lookup,
                                    security_headers)
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

# --------------------------------------------------- social profile lookups

GITLAB_HIT = [{"id": 7, "username": "fixturehandle", "name": "Fix Ture",
               "created_at": "2019-04-01T00:00:00.000Z", "public_email": None}]

MASTODON_HIT = {"id": 123, "username": "fixturehandle", "display_name": "Fx",
                "url": "https://mastodon.social/@fixturehandle",
                "created_at": "2020-02-02T00:00:00.000Z"}

BLUESKY_HIT = {"did": "did:plc:fixture", "handle": "fixturehandle.bsky.social",
               "displayName": "Fx", "createdAt": "2021-01-01T00:00:00.000Z"}


async def test_gitlab_profile_confirms_exact_match(router, transport):
    router.add_host_path("gitlab.com", "/api/v4/users",
                         httpx.Response(200, json=GITLAB_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await gitlab_profile.run(client, "fixturehandle")
    assert res["error"] is None
    urls = [p for p in res["pivots"] if p["type"] == "url"]
    assert urls and urls[0]["value"] == "https://gitlab.com/fixturehandle"
    assert any(p["type"] == "name" and p["value"] == "Fix Ture"
               for p in res["pivots"])


async def test_gitlab_profile_silent_when_absent(router, transport):
    router.add_host_path("gitlab.com", "/api/v4/users",
                         httpx.Response(200, json=[]))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await gitlab_profile.run(client, "missing-handle")
    assert res["error"] is None and res["pivots"] == []


async def test_mastodon_lookup_confirms_and_404_is_silence(router, transport):
    router.add_host_path("mastodon.social", "/api/v1/accounts/lookup",
                         httpx.Response(200, json=MASTODON_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await mastodon_lookup.run(client, "fixturehandle")
    assert res["error"] is None
    assert any(p["value"] == "https://mastodon.social/@fixturehandle"
               for p in res["pivots"])

    router.add_host_path("mastodon.social", "/api/v1/accounts/lookup",
                         httpx.Response(404, json={"error": "Record not found"}))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await mastodon_lookup.run(client, "missing-handle")
    assert res["error"] is None and res["pivots"] == []


async def test_bluesky_profile_confirms_and_error_payload_is_silence(router, transport):
    router.add_host_path("public.api.bsky.app",
                         "/xrpc/app.bsky.actor.getProfile",
                         httpx.Response(200, json=BLUESKY_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await bluesky_profile.run(client, "fixturehandle")
    assert res["error"] is None
    assert any(p["value"] == "https://bsky.app/profile/fixturehandle.bsky.social"
               for p in res["pivots"])

    router.add_host_path("public.api.bsky.app",
                         "/xrpc/app.bsky.actor.getProfile",
                         httpx.Response(400, json={"error": "InvalidRequest",
                                                   "message": "Profile not found"}))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await bluesky_profile.run(client, "missing-handle")
    assert res["error"] is None and res["pivots"] == []


HN_HIT = {"username": "fixturehandle", "karma": 1420, "about": "fixture bio"}

DOCKER_HIT = {"orgname": "fixturehandle", "full_name": "Fix Ture",
              "date_joined": "2020-06-01T00:00:00.000Z", "type": "User"}

CHESS_HIT = {"player_id": 42, "name": "Fix Ture", "url": "https://www.chess.com/member/fixturehandle",
             "joined": "2018-03-03"}


async def test_hackernews_user_confirms(router, transport):
    router.add_host_path("hn.algolia.com", "/api/v1/users/fixturehandle",
                         httpx.Response(200, json=HN_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await hackernews_user.run(client, "fixturehandle")
    assert res["error"] is None
    assert any(p["value"] == "https://news.ycombinator.com/user?id=fixturehandle"
               for p in res["pivots"])
    assert any("karma 1420" in p["value"] for p in res["pivots"])

    router.add_host_path("hn.algolia.com", "/api/v1/users/missing-handle",
                         httpx.Response(404, json={"error": "not found"}))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await hackernews_user.run(client, "missing-handle")
    assert res["error"] is None and res["pivots"] == []


async def test_dockerhub_profile_confirms(router, transport):
    router.add_host_path("hub.docker.com", "/v2/users/fixturehandle",
                         httpx.Response(200, json=DOCKER_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await dockerhub_profile.run(client, "fixturehandle")
    assert res["error"] is None
    assert any(p["type"] == "name" and p["value"] == "Fix Ture"
               for p in res["pivots"])
    assert any("created 2020-06-01" in p["value"] for p in res["pivots"])


async def test_chess_profile_confirms(router, transport):
    router.add_host_path("api.chess.com", "/pub/player/fixturehandle",
                         httpx.Response(200, json=CHESS_HIT))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await chess_profile.run(client, "fixturehandle")
    assert res["error"] is None
    assert any(p["value"] == "https://www.chess.com/member/fixturehandle"
               for p in res["pivots"])
    assert any(p["type"] == "name" for p in res["pivots"])


async def test_chess_profile_handles_unix_timestamp_joined(router, transport):
    # the live API returns `joined` as an int timestamp, not a string
    payload = dict(CHESS_HIT)
    payload["joined"] = 1514923445
    router.add_host_path("api.chess.com", "/pub/player/fixturehandle",
                         httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await chess_profile.run(client, "fixturehandle")
    assert res["error"] is None
    assert any("chess.com account created 2018-01" in p["value"]
               for p in res["pivots"])


async def test_security_headers_reports_present_and_missing(router, transport):
    router.add_host_path("fixture.example", "/",
                         httpx.Response(200, headers={
                             "strict-transport-security": "max-age=31536000",
                         }, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await security_headers.run(client, "fixture.example")
    assert res["error"] is None
    assert any("sets strict-transport-security" in p["value"] for p in res["pivots"])
    assert any("lacks content-security-policy" in p["value"] for p in res["pivots"])
    summary = [p for p in res["pivots"] if "security headers:" in p["value"]]
    assert summary and "1/6" in summary[0]["value"]

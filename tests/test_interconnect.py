"""Regression tests for the account-interconnection modules:

- github-commit-search: queries the seed email itself against GitHub's
  commit-search API (an exact author-email field match, not a local-part
  guess) and only turns a real "User"-typed commit author/committer login
  into a pivot.
- github-profile / keybase-proofs: a conditional step that only triggers
  on an already-confirmed github.com/<user> profile-root URL pivot, never
  on a guessed username.

A 403/429 from any of these APIs must be reported as an error, never
collapsed into silence."""

from __future__ import annotations

import hashlib

import httpx

from thousand_eyes.modules import github_commit_search, github_profile, keybase_proofs
from thousand_eyes.scan import run_scan

FIXTURE_EMAIL = "fixture.subject@example.org"


def _gravatar_digest(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


# --- extract_github_username: strict parse, never a guess -----------------

def test_extract_github_username_accepts_profile_root_only():
    assert github_profile.extract_github_username("https://github.com/fixtureuser") == "fixtureuser"
    assert github_profile.extract_github_username("https://github.com/fixtureuser/") == "fixtureuser"


def test_extract_github_username_rejects_non_profile_paths():
    assert github_profile.extract_github_username("https://github.com/fixtureuser/some-repo") is None
    assert github_profile.extract_github_username("https://github.com/fixtureuser/some-repo/issues/1") is None
    assert github_profile.extract_github_username("https://gitlab.com/fixtureuser") is None
    assert github_profile.extract_github_username("https://github.com") is None


# --- github_commit_search.run: exact author-email field match only --------

async def test_github_commit_search_reports_real_user_logins_only(router, transport):
    router.add_host_path(
        "api.github.com",
        "/search/commits",
        httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "html_url": "https://github.com/fixture-org/repo/commit/abc123",
                        "author": {"login": "fixtureuser", "type": "User"},
                        "committer": {"login": "web-flow", "type": "User"},
                    }
                ],
            },
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_commit_search.run(client, FIXTURE_EMAIL)

    assert res["error"] is None
    values = {p["value"] for p in res["pivots"]}
    assert "https://github.com/fixtureuser" in values
    assert "https://github.com/web-flow" in values
    for p in res["pivots"]:
        assert p["type"] == "url"
        assert p["evidence_url"] == "https://github.com/fixture-org/repo/commit/abc123"


async def test_github_commit_search_skips_bot_and_non_user_authors(router, transport):
    router.add_host_path(
        "api.github.com",
        "/search/commits",
        httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "html_url": "https://github.com/fixture-org/repo/commit/abc123",
                        "author": {"login": "some-bot", "type": "Bot"},
                        "committer": None,
                    }
                ],
            },
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_commit_search.run(client, FIXTURE_EMAIL)

    assert res == {"pivots": [], "error": None}


async def test_github_commit_search_zero_results_is_silence(router, transport):
    router.add_host_path(
        "api.github.com",
        "/search/commits",
        httpx.Response(200, json={"total_count": 0, "items": []}),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_commit_search.run(client, FIXTURE_EMAIL)

    assert res == {"pivots": [], "error": None}


async def test_github_commit_search_rate_limit_is_reported_as_error(router, transport):
    router.add_host_path("api.github.com", "/search/commits", httpx.Response(403))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_commit_search.run(client, FIXTURE_EMAIL)

    assert res["pivots"] == []
    assert res["error"] is not None
    assert "rate limited" in res["error"]


# --- github_profile.run: real API facts only -------------------------------

async def test_github_profile_reports_only_fields_present(router, transport):
    router.add_host_path(
        "api.github.com",
        "/users/fixtureuser",
        httpx.Response(
            200,
            json={
                "name": "Fixture User",
                "blog": "fixtureuser.example",
                "twitter_username": "fixturehandle",
                "email": None,
            },
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_profile.run(client, "fixtureuser")

    assert res["error"] is None
    values_by_type = {}
    for p in res["pivots"]:
        values_by_type.setdefault(p["type"], []).append(p["value"])

    assert "Fixture User" in values_by_type.get("name", [])
    assert "https://fixtureuser.example" in values_by_type.get("url", [])
    assert "https://twitter.com/fixturehandle" in values_by_type.get("url", [])
    assert "email" not in values_by_type  # null field -> no fabricated pivot


async def test_github_profile_404_is_silence_not_error(router, transport):
    router.add_host_path("api.github.com", "/users/fixtureuser", httpx.Response(404))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_profile.run(client, "fixtureuser")

    assert res == {"pivots": [], "error": None}


async def test_github_profile_rate_limit_is_reported_as_error(router, transport):
    router.add_host_path("api.github.com", "/users/fixtureuser", httpx.Response(403))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await github_profile.run(client, "fixtureuser")

    assert res["pivots"] == []
    assert res["error"] is not None
    assert "rate limited" in res["error"]


# --- keybase_proofs.run: only live, URL-backed proofs ----------------------

async def test_keybase_proofs_reports_only_live_url_backed_proofs(router, transport):
    router.add_host_path(
        "keybase.io",
        "/_/api/1.0/user/lookup.json",
        httpx.Response(
            200,
            json={
                "status": {"code": 0},
                # Keybase's real API returns `them` as a LIST of candidate
                # matches, confirmed live against api.1.0/user/lookup.json.
                "them": [
                    {
                        "proofs_summary": {
                            "all": [
                                {
                                    "proof_type": "twitter",
                                    "nametag": "fixturehandle",
                                    "state": 1,
                                    "proof_url": "https://twitter.com/fixturehandle/status/123",
                                    "sig_id": "abc123",
                                },
                                {
                                    "proof_type": "reddit",
                                    "nametag": "fixturehandle2",
                                    "state": 0,  # not live -> must be excluded
                                    "proof_url": "https://reddit.com/r/foo/comments/1",
                                    "sig_id": "def456",
                                },
                                {
                                    # dns proofs point back at keybase.io's own
                                    # sigchain viewer, not an independently
                                    # checkable third-party URL -> excluded.
                                    "proof_type": "dns",
                                    "nametag": "fixture-corp.example",
                                    "state": 1,
                                    "proof_url": "https://keybase.io/fixtureuser/sigchain#deadbeef",
                                    "sig_id": "ghi789",
                                },
                            ]
                        }
                    }
                ],
            },
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await keybase_proofs.run(client, "fixtureuser")

    assert res["error"] is None
    assert len(res["pivots"]) == 1
    pivot = res["pivots"][0]
    assert pivot["value"] == "https://twitter.com/fixturehandle/status/123"
    assert pivot["evidence_url"] == pivot["value"]
    assert "abc123" in pivot["snippet"]


async def test_keybase_proofs_no_matching_user_is_silence(router, transport):
    router.add_host_path(
        "keybase.io",
        "/_/api/1.0/user/lookup.json",
        httpx.Response(200, json={"status": {"code": 0}, "them": []}),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await keybase_proofs.run(client, "fixtureuser")

    assert res == {"pivots": [], "error": None}


async def test_keybase_proofs_rate_limit_is_reported_as_error(router, transport):
    router.add_host_path("keybase.io", "/_/api/1.0/user/lookup.json", httpx.Response(429))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await keybase_proofs.run(client, "fixtureuser")

    assert res["pivots"] == []
    assert res["error"] is not None
    assert "rate limited" in res["error"]


# --- full scan wiring: only fires off a confirmed github.com profile pivot -

async def test_scan_only_triggers_interconnect_on_confirmed_github_profile_pivot(router, transport):
    digest = _gravatar_digest(FIXTURE_EMAIL)
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(
            200,
            json={
                "entry": [
                    {
                        "accounts": [
                            {"shortname": "github", "url": "https://github.com/fixtureuser"},
                        ],
                    }
                ]
            },
        ),
    )
    router.add_host_path("api.github.com", "/search/commits", httpx.Response(200, json={"total_count": 0, "items": []}))
    router.add_host_path("api.github.com", "/users/fixtureuser", httpx.Response(404))
    router.add_host_path("keybase.io", "/_/api/1.0/user/lookup.json", httpx.Response(200, json={"status": {"code": 0}, "them": []}))
    # depth-2 fetch of the github profile URL itself (existing fetch module path)
    router.add_host_path("github.com", "/fixtureuser", httpx.Response(200, text="<html></html>"))

    result = await run_scan(FIXTURE_EMAIL, transport=transport)

    github_profile_calls = [u for u in router.requested_urls if "api.github.com/users/" in u]
    keybase_calls = [u for u in router.requested_urls if "keybase.io" in u]
    assert len(github_profile_calls) == 1
    assert len(keybase_calls) == 1
    # No node was derived from the email local-part -- the username came
    # only from the already-confirmed gravatar github.com URL pivot.
    node_values = {n["value"] for n in result.to_dict()["nodes"]}
    assert "fixture.subject" not in node_values


async def test_scan_does_not_trigger_interconnect_for_non_profile_github_url(router, transport):
    digest = _gravatar_digest(FIXTURE_EMAIL)
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(
            200,
            json={
                "entry": [
                    {
                        "accounts": [
                            {"shortname": "github", "url": "https://github.com/fixtureuser/some-repo"},
                        ],
                    }
                ]
            },
        ),
    )
    router.add_host_path("github.com", "/fixtureuser/some-repo", httpx.Response(200, text="<html></html>"))
    # github-commit-search always runs for an email seed (not gated on a
    # prior pivot) -- give it an empty result so it doesn't add its own
    # github.com pivot and confound this test's assertion.
    router.add_host_path("api.github.com", "/search/commits", httpx.Response(200, json={"total_count": 0, "items": []}))

    await run_scan(FIXTURE_EMAIL, transport=transport)

    assert not any("api.github.com/users/" in u for u in router.requested_urls)
    assert not any("keybase.io" in u for u in router.requested_urls)

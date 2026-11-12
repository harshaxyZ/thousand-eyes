"""The 5 mandatory acceptance tests from the spec, plus hard-rule #10's
explicit "unknown fixture address -> zero pivots" case.

All network access is replaced by an httpx.MockTransport fixture (see
conftest.py). No test asserts a real person's data -- every identifier
below is a fixture value that does not correspond to a real account.

Note on test 1: the spec's prose example says a gravatar hit with a name
AND a url yields "two nodes and one edge." That undercounts relative to
hard rule #1 ("a finding needs evidence: ... no evidence, no line") --
a name pivot and a url pivot are two independent findings, so each gets
its own edge. This test enforces hard rule #1 literally: two nodes, two
edges, and critically, no third node for a *guessed* username (the
property the prose example is actually checking for).
"""

from __future__ import annotations

import hashlib
import httpx
import pytest

from thousand_eyes import cli
from thousand_eyes.modules import exact_document, registration_leaves
from thousand_eyes.scan import run_scan

FIXTURE_EMAIL = "fixture.subject@example.org"


def _gravatar_digest(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


# 1. gravatar returns a name and one twitter URL -> exactly those two
#    nodes/edges appear, and no guessed username node is ever created.
async def test_gravatar_name_and_url_produce_evidence_backed_nodes_only(router, transport):
    digest = _gravatar_digest(FIXTURE_EMAIL)
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(
            200,
            json={
                "entry": [
                    {
                        "displayName": "Fixture Person",
                        "accounts": [
                            {"shortname": "twitter", "url": "https://twitter.com/fixtureperson"},
                        ],
                    }
                ]
            },
        ),
    )

    result = await run_scan(FIXTURE_EMAIL, transport=transport)
    data = result.to_dict()

    node_values = {n["value"] for n in data["nodes"]}
    # v0.3: the seed itself is now a graph node (bug fix - it used to be
    # silently missing from the JSON/DOT exports)
    assert node_values == {FIXTURE_EMAIL, "Fixture Person", "https://twitter.com/fixtureperson"}
    assert len(data["edges"]) == 2
    for edge in data["edges"]:
        assert edge["source"] and edge["evidence_url"] and edge["snippet"]
        assert edge["from"] == FIXTURE_EMAIL

    # No node was derived from the email local-part ("fixture.subject").
    assert "fixture.subject" not in node_values
    assert not any(n["type"] == "handle" for n in data["nodes"])


# 2. gravatar 404, hibp empty, no documents -> "no public pivot", exit 0.
# Also covers hard rule #10: an address the fixture marks unknown must
# produce zero pivots.
async def test_unknown_address_produces_no_public_pivot(router, transport, monkeypatch, capsys):
    monkeypatch.setenv("HIBP_API_KEY", "fixture-key")
    digest = _gravatar_digest(FIXTURE_EMAIL)
    # Gravatar: unregistered hash -> 404 (silence, not an error).
    router.add_host_path("www.gravatar.com", f"/{digest}.json", httpx.Response(404))
    # HIBP: no breaches for this account -> 404.
    router.add_host_path("haveibeenpwned.com", f"/api/v3/breachedaccount/{FIXTURE_EMAIL}", httpx.Response(404))

    result = await run_scan(FIXTURE_EMAIL, transport=transport)
    data = result.to_dict()

    # v0.3: the seed is itself a graph node now (bug fix in models), so an
    # empty scan contains exactly one node - the seed - and zero edges.
    assert data["nodes"] == [{"class": "pivot", "type": "email", "value": FIXTURE_EMAIL}]
    assert data["edges"] == []

    from thousand_eyes.output import render_text

    text = render_text(result)
    assert "no public pivot" in text


def test_help_works_with_no_network_and_no_keys(monkeypatch, capsys):
    for var in ("HIBP_API_KEY", "BRAVE_API_KEY", "GOOGLE_CSE_API_KEY", "GOOGLE_CSE_ID"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: thousand-eyes" in out


# 3. a registration site returns 200 for every address (bare 200, no
# marker match) -> that site is NOT listed. This is exactly the
# sherlock-style false positive the spec calls out.
async def test_bare_200_without_marker_match_is_not_a_leaf(router, transport, monkeypatch):
    fake_sites = [
        {
            "name": "FixtureSite",
            "category": "test",
            "url_template": "https://fixture-site.example/check?u={value}",
            "registered_marker": "ACCOUNT_FOUND_MARKER",
            "absent_marker": "ACCOUNT_NOT_FOUND_MARKER",
        }
    ]
    monkeypatch.setattr(registration_leaves, "_load_sites", lambda: fake_sites)

    # The site returns a bland 200 for every address, with neither marker
    # present in the body -- a Sherlock-style checker would wrongly call
    # this "registered."
    router.add_host_path("fixture-site.example", "/check", httpx.Response(200, text="<html>welcome</html>"))

    async with httpx.AsyncClient(transport=transport) as client:
        res = await registration_leaves.run(client, FIXTURE_EMAIL)

    assert res["leaves"] == []
    assert res["errors"] == []


# 4. a document contains the email and a phone -> the phone is a node;
# a random name on the same page is NOT a node (names are never extracted
# by the document/fetch extraction rules at all).
async def test_document_extracts_phone_but_never_names(router, transport, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fixture-key")

    page_url = "https://pages.example/profile"
    router.add_host_path(
        "api.search.brave.com",
        "/res/v1/web/search",
        httpx.Response(200, json={"web": {"results": [{"url": page_url}]}}),
    )
    router.add_host_path(
        "pages.example",
        "/profile",
        httpx.Response(
            200,
            text=(
                f"<html><body>Contact {FIXTURE_EMAIL} or call +15551234567. "
                f"Also seen near here: Totally Unrelated Person Name.</body></html>"
            ),
        ),
    )

    async with httpx.AsyncClient(transport=transport) as client:
        res = await exact_document.run(client, FIXTURE_EMAIL)

    # v0.3 contract: per-page failures are accumulated in `errors` (a single
    # dead result page no longer aborts the whole module), and the module
    # reports which provider served the search.
    assert res["errors"] == []
    assert res["skipped"] is None

    values_by_type = {}
    for p in res["pivots"]:
        values_by_type.setdefault(p["type"], []).append(p["value"])

    assert "+15551234567" in values_by_type.get("phone", [])
    # extract.py has no name-extraction logic at all -- this is guaranteed
    # structurally, not just by this fixture's content.
    assert "name" not in values_by_type
    assert not any("Unrelated Person Name" in v for vs in values_by_type.values() for v in vs)

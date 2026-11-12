"""Cascade tests: handle extraction, stage accounting, live events and the
cascade actually spreading from one lead to the next."""

from __future__ import annotations

import httpx

from thousand_eyes.handle_rules import extract_handle
from thousand_eyes.scan import run_scan

FIXTURE_EMAIL = "fixture.subject@example.org"


# ---------------------------------------------------------- handle rules

def test_handle_is_extracted_from_confirmed_profile_urls():
    assert extract_handle("https://github.com/fixturehandle") == "fixturehandle"
    assert extract_handle("https://x.com/fixturehandle") == "fixturehandle"
    assert extract_handle("https://gitlab.com/fixturehandle") == "fixturehandle"
    assert extract_handle("https://bsky.app/profile/fixturehandle.bsky.social") == "fixturehandle"
    assert extract_handle("https://www.chess.com/member/fixturehandle") == "fixturehandle"
    assert extract_handle("https://lichess.org/@/fixturehandle") == "fixturehandle"
    assert extract_handle("https://hub.docker.com/u/fixturehandle") == "fixturehandle"
    assert extract_handle("https://news.ycombinator.com/user?id=fixturehandle") == "fixturehandle"


def test_non_profile_urls_yield_no_handle():
    assert extract_handle("https://www.webuildnow.in/") == ""
    assert extract_handle("https://web.archive.org/web/2024/https://github.com/x") == ""
    assert extract_handle("") == ""
    assert extract_handle("not a url") == ""


# --------------------------------------------------------------- cascade

async def test_profile_url_becomes_the_next_username_lead(router, transport):
    """The hinge of the cascade: a confirmed profile URL proves the handle,
    so the handle becomes a lead and the sweep continues from there."""
    import hashlib

    digest = hashlib.md5(FIXTURE_EMAIL.strip().lower().encode("utf-8")).hexdigest()
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(200, json={"entry": [{
            "accounts": [{"shortname": "x",
                          "url": "https://social.example/fixtureperson"}]}]}),
    )
    router.add_host_path(
        "social.example", "/fixtureperson",
        httpx.Response(200, text="profile page"),
    )

    stages: list[dict] = []
    events: list[tuple[str, dict]] = []

    def on_event(event):
        events.append(event)

    result = await run_scan(FIXTURE_EMAIL, transport=transport, max_depth=2,
                             on_event=on_event)
    stages = result.meta.get("stages") or []

    assert stages, "the run must record its stages"
    assert stages[0]["type"] == "email"
    # the profile URL from gravatar is a node
    assert any(n.value == "https://social.example/fixtureperson"
               for n in result.nodes.values())
    # and the live feed told us what happened
    kinds = {k for k, _ in events}
    assert "stage_start" in kinds and "module" in kinds
    assert any(k == "found" for k, _ in events)


async def test_stage_table_records_what_each_stage_did(router, transport):
    import hashlib

    digest = hashlib.md5(FIXTURE_EMAIL.strip().lower().encode("utf-8")).hexdigest()
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(200, json={"entry": [{
            "accounts": [{"shortname": "x",
                          "url": "https://social.example/fixtureperson"}]}]}),
    )
    router.add_host_path(
        "social.example", "/fixtureperson",
        httpx.Response(200, text="profile page"),
    )

    result = await run_scan(FIXTURE_EMAIL, transport=transport, max_depth=1)
    stages = result.meta.get("stages") or []
    assert stages[0]["modules"] > 0
    assert stages[0]["findings"] >= 1
    assert "seconds" in stages[0]

    from thousand_eyes.output import render_text
    text = render_text(result, version="0.7.0")
    assert "what it did" in text
    assert FIXTURE_EMAIL in text


async def test_cascade_stops_cleanly_on_a_lead_budget(router, transport):
    import hashlib

    digest = hashlib.md5(FIXTURE_EMAIL.strip().lower().encode("utf-8")).hexdigest()
    router.add_host_path(
        "www.gravatar.com",
        f"/{digest}.json",
        httpx.Response(200, json={"entry": [{
            "accounts": [{"shortname": "x",
                          "url": "https://social.example/fixtureperson"}]}]}),
    )
    router.add_host_path(
        "social.example", "/fixtureperson",
        httpx.Response(200, text="profile page"),
    )

    result = await run_scan(FIXTURE_EMAIL, transport=transport, max_depth=3,
                             max_leads=1)
    assert result.meta.get("stages")
    assert len(result.meta["stages"]) == 1
    assert "stopped" in result.meta
    assert "lead budget" in result.meta["stopped"]


async def test_registry_covers_every_lead_type():
    from thousand_eyes.registry import MODULES, modules_for
    for lead_type in ("email", "username", "url", "domain", "phone"):
        assert modules_for(lead_type), f"no module for {lead_type}"
    # the agent tool layer is generated from the same registry
    from thousand_eyes.agent.tools import ENGINE_TOOLS
    assert len(ENGINE_TOOLS) >= len(MODULES)
    names = {t.name for t in ENGINE_TOOLS}
    for spec in MODULES:
        assert spec.tool_name in names

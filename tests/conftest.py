"""Fixture HTTP transport for hermetic tests.

Hard rule #10: tests must use a fixture HTTP server, never the live
network, and no test may assert a real person's data.

`FixtureRouter` intercepts httpx at the transport layer (httpx.MockTransport)
so the full module/pipeline code runs unmodified against canned responses.
Matching is by (host, path) rather than the full URL string, since query
string encoding/ordering is an httpx implementation detail we don't want
tests pinned to.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest


Predicate = Callable[[httpx.Request], bool]


class FixtureRouter:
    def __init__(self):
        self._matchers: list[tuple[Predicate, httpx.Response]] = []
        self.requested_urls: list[str] = []

    def add(self, predicate: Predicate, response: httpx.Response) -> None:
        self._matchers.append((predicate, response))

    def add_host_path(self, host: str, path: str, response: httpx.Response) -> None:
        self.add(lambda r, host=host, path=path: r.url.host == host and r.url.path == path, response)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested_urls.append(str(request.url))
        for predicate, response in self._matchers:
            if predicate(request):
                return response
        # Unconfigured route: behaves like "nothing here" (404, empty body).
        # This is deliberate -- a module seeing an unmatched route must drop
        # it (neither marker present) rather than erroring the whole scan.
        return httpx.Response(404, text="")


@pytest.fixture
def router() -> FixtureRouter:
    return FixtureRouter()


@pytest.fixture
def transport(router: FixtureRouter) -> httpx.MockTransport:
    return httpx.MockTransport(router)


@pytest.fixture(autouse=True)
def _stable_loop_policy():
    """Pin one asyncio loop policy for the whole session.

    pytest-asyncio's per-test policy restore churns deprecated default-policy
    APIs on Python 3.14 and intermittently leaves the next App.run_test()
    with a torn-down loop (QueryError on a healthy app). One stable policy
    for the session removes the race without touching test semantics."""
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    yield


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch):
    """Every test runs with zero API keys configured unless it explicitly
    sets one -- a stray key in the real environment must never make a test
    hit the live network or change its expected behaviour."""
    for var in (
        "HIBP_API_KEY", "BRAVE_API_KEY", "GOOGLE_CSE_API_KEY", "GOOGLE_CSE_ID", "GITHUB_TOKEN",
        "SERPER_API_KEY", "HUDSON_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def patch_cli_transport(monkeypatch, transport: httpx.MockTransport):
    """Force thousand_eyes.scan.new_client to use the fixture transport even
    when called without one (i.e. through cli.main, which never passes a
    test-only transport argument). Keeps CLI-level tests fully offline."""
    import thousand_eyes.scan as scan_module
    from thousand_eyes.http_client import new_client as real_new_client

    def fake_new_client(transport_arg=None, **_kwargs):
        return real_new_client(transport=transport)

    monkeypatch.setattr(scan_module, "new_client", fake_new_client)

"""Single shared httpx client factory. No retries that could look like
brute-forcing, no credential handling, no auth attempts - read-only GETs."""

from __future__ import annotations

import httpx

USER_AGENT = "thousand-eyes/0.3 (self-audit tool; reads only public first-party endpoints)"

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def new_client(transport: httpx.AsyncBaseTransport | None = None, url_log_path: str | None = None) -> httpx.AsyncClient:
    """`transport` is overridden by tests with an httpx.MockTransport so the
    full module pipeline runs against a fixture, never the live network.
    `url_log_path` (--url-log) appends every outbound URL to a file — the
    full-request-transparency safety check reads exactly that log."""
    hooks = None
    if url_log_path:
        async def _log(request: httpx.Request) -> None:
            with open(url_log_path, "a", encoding="utf-8") as fh:
                fh.write(str(request.url) + "\n")
        hooks = {"request": [_log]}
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        transport=transport,
        event_hooks=hooks,
    )


def describe_http_error(exc: httpx.HTTPError) -> str:
    """Some httpx exceptions (notably timeouts) stringify to an empty string.
    Hard rule #7 requires printing the actual error, not a blank -- always
    prefix with the exception's class name so the line is never empty."""
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__

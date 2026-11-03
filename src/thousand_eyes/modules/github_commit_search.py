"""Module (email-only): github-commit-search.

Runs directly against the seed email -- this is not a local-part guess
(hard rule #2 forbids deriving a username/handle from the local-part, not
querying the seed email itself, which every other email-seed module in
this pipeline already does). GitHub's commit-search API is queried with
`author-email:<exact seed email>`, an exact field match, not a keyword
search -- confirmed live to return `items: []` for an email with zero
real commits, and real commit/author data for one that has them. This is
the same "does a live source actually confirm this exact string" check
that exact-document.py already performs against the open web, just
scoped to GitHub's commit corpus -- so a hit here is account-interconnect
evidence (a real commit by this real author-email), not a guess.

Only the GitHub **login** of the commit's author/committer (an actual
account GitHub itself attributes the commit to) becomes a pivot, as a
github.com profile-root URL -- never the commit's free-text author name
(which could be anyone's local git config and isn't itself a verifiable
account). A missing/bot/non-"User" author is skipped, not guessed at.

Unauthenticated rate limit for the search endpoint is 10 requests/minute
(confirmed via X-RateLimit-Limit); set GITHUB_TOKEN to raise it. A
403/429 is reported as an error, never silently dropped (hard rule #7).
"""

from __future__ import annotations

import os

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "github-commit-search"
API_URL = "https://api.github.com/search/commits"
MAX_RESULTS = 5


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.get(
            API_URL,
            headers=headers,
            params={"q": f"author-email:{email}", "per_page": MAX_RESULTS},
        )
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    items = data.get("items") or []
    pivots: list[dict] = []
    seen_logins: set[str] = set()

    for item in items[:MAX_RESULTS]:
        commit_url = item.get("html_url") or item.get("url")
        if not commit_url:
            continue

        for role in ("author", "committer"):
            account = item.get(role)
            if not isinstance(account, dict):
                continue
            if account.get("type") != "User":
                continue
            login = (account.get("login") or "").strip()
            if not login or login.lower() in seen_logins:
                continue
            seen_logins.add(login.lower())

            pivots.append({
                "type": "url",
                "value": f"https://github.com/{login}",
                "source": MODULE,
                "evidence_url": commit_url,
                "snippet": (
                    f'commit {role} login="{login}" for a commit with '
                    f'author-email="{email}"'
                ),
                "status": resp.status_code,
            })

    return {"pivots": pivots, "error": None}

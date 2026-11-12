"""Module (conditional interconnect step): github-profile.

Only runs against a URL that an earlier module already returned as
evidence AND that parses as exactly https://github.com/<username> (profile
root - not a repo, gist, or org sub-path). Never derives a username by
guessing or from the email local-part (hard rule #2); the caller only
calls run() with a username pulled from an already-evidence-backed URL via
extract_github_username()'s strict regex.

Calls GitHub's public, unauthenticated REST user endpoint (60 req/hr/IP,
higher with an optional GITHUB_TOKEN) and reports only fields the API
actually returned: real name, personal blog/website URL, Twitter handle,
and public email - each a literal account-API fact, never a guess. A 404
means the account doesn't exist (silence, not an error). A 403/429 is a
rate limit and must be reported as an error, never collapsed into "nothing
found" (hard rule #7).
"""

from __future__ import annotations

import os
import re

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "github-profile"
API_URL = "https://api.github.com/users/{username}"

_GITHUB_PROFILE_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?$"
)


def extract_github_username(url: str) -> str | None:
    """Returns a username only if `url` is exactly a GitHub profile root
    URL. Returns None for anything else (repo/gist/org paths, other
    hosts) - this is a strict parse of an already-confirmed evidence URL,
    never a guess."""
    match = _GITHUB_PROFILE_URL_RE.match(url.strip())
    return match.group(1) if match else None


async def run(client: httpx.AsyncClient, username: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = API_URL.format(username=username)
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code == 404:
        return {"pivots": [], "error": None}

    if resp.status_code in (403, 429):
        return {"pivots": [], "error": f"{MODULE}: rate limited (status {resp.status_code})"}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    pivots: list[dict] = []

    name = (data.get("name") or "").strip()
    if name:
        pivots.append({
            "type": "name",
            "value": name,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'GitHub profile name="{name}"',
            "status": resp.status_code,
        })

    raw_blog = (data.get("blog") or "").strip()
    if raw_blog:
        blog_url = raw_blog if raw_blog.startswith(("http://", "https://")) else f"https://{raw_blog}"
        if blog_url.startswith("https://"):
            pivots.append({
                "type": "url",
                "value": blog_url,
                "source": MODULE,
                "evidence_url": url,
                "snippet": f'GitHub profile blog field="{raw_blog}"',
                "status": resp.status_code,
            })

    twitter = (data.get("twitter_username") or "").strip()
    if twitter:
        twitter_url = f"https://twitter.com/{twitter}"
        pivots.append({
            "type": "url",
            "value": twitter_url,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'GitHub profile twitter_username="{twitter}"',
            "status": resp.status_code,
        })

    email = (data.get("email") or "").strip()
    if email:
        pivots.append({
            "type": "email",
            "value": email,
            "source": MODULE,
            "evidence_url": url,
            "snippet": f'GitHub profile public email="{email}"',
            "status": resp.status_code,
        })

    return {"pivots": pivots, "error": None}

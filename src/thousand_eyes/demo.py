"""--demo mode: a fully offline fixture transport.

`thousand-eyes --demo <seeds>` replays canned SYNTHETIC responses through
httpx.MockTransport — no socket is opened in either direction. Everything
keyless is exercised (gravatar, twitter availability, userrealm, breach
existence, pgp, github commit search + profile + keybase, reddit,
wayback, DNS/MX/crt.sh/RDAP, duolingo leaf, WMN username sweep via
host-matched fixtures, bing search page, profile-page wildfire hops).

The demo subject is fictional: demo.subject@example.org / demosubject.
Presentation-safe: your own identifiers are never even used in fixtures.
"""

from __future__ import annotations

import hashlib

import httpx

DEMO_EMAIL = "demo.subject@example.org"
DEMO_USER = "demosubject"
DEMO_DOMAIN = "example.org"


def _digest(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


def _pitch(text: str) -> str:
    return httpx.Response(200, text=text)


def demo_transport() -> httpx.MockTransport:
    digest = _digest(DEMO_EMAIL)

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url

        if url.host == "www.gravatar.com" and url.path == f"/{digest}.json":
            return httpx.Response(200, json={
                "entry": [{
                    "displayName": "Demo Subject",
                    "accounts": [
                        {"shortname": "github", "url": f"https://github.com/{DEMO_USER}"},
                        {"shortname": "twitter", "url": f"https://twitter.com/{DEMO_USER}"},
                    ],
                }]
            })

        if url.host == "api.twitter.com" and url.path == "/i/users/email_available.json":
            return httpx.Response(200, json={"valid": True, "msg": "Unavailable.", "taken": True})

        if url.host == "login.microsoftonline.com" and url.path.startswith("/common/userrealm"):
            return httpx.Response(200, json={
                "ver": "1.0",
                "account_type": "Managed",
                "domain_name": DEMO_DOMAIN,
                "cloud_instance_name": "microsoftonline.com",
            })

        if url.host == "api.xposedornot.com" and url.path.startswith("/v1/check-email"):
            return httpx.Response(200, json={
                "breaches_details": {
                    "breaches": {
                        "Collection1": {"breach_date": "2019-01-07", "pwn_count": 5947009},
                        "DemoLeak": {"breach_date": "2021-04-01", "pwn_count": 25109},
                    }
                }
            })

        if url.host == "keys.openpgp.org":
            return httpx.Response(404, text="No key found for email address")

        if url.host == "api.github.com":
            if url.path == "/search/commits":
                return httpx.Response(200, json={
                    "total_count": 1,
                    "items": [{
                        "html_url": f"https://github.com/demo-org/demo-repo/commit/abc123",
                        "author": {"login": DEMO_USER, "type": "User"},
                        "committer": {"login": DEMO_USER, "type": "User"},
                    }],
                })
            if url.path == f"/users/{DEMO_USER}":
                return httpx.Response(200, json={
                    "name": "Demo Subject",
                    "blog": "https://demo.subject.dev",
                    "twitter_username": DEMO_USER,
                    "email": None,
                })
            return httpx.Response(404)

        if url.host == "keybase.io" and url.path == "/_/api/1.0/user/lookup.json":
            return httpx.Response(200, json={
                "status": {"code": 0},
                "them": [{
                    "proofs_summary": {
                        "all": [{
                            "proof_type": "twitter",
                            "nametag": DEMO_USER,
                            "state": 1,
                            "proof_url": f"https://twitter.com/{DEMO_USER}/status/123",
                            "sig_id": "demo-sig-1",
                        }],
                    }
                }],
            })

        if url.host == "github.com" and url.path == f"/{DEMO_USER}":
            return _pitch(
                "<html><body>Demo Subject — contact me at "
                f"{DEMO_EMAIL} or {DEMO_USER} on instagram. "
                "I also post at https://mastodon.social/@demosubject "
                "and https://demo.subject.dev"
                "</body></html>"
            )

        if url.host == "www.reddit.com" and url.path == f"/user/{DEMO_USER}/about.json":
            return httpx.Response(200, json={
                "data": {"name": DEMO_USER, "created_utc": 1614556800, "total_karma": 4212}
            })

        if url.host == "mastodon.social" and url.path == "/@demosubject":
            return _pitch(
                f"<html><body>Demo Subject (@demosubject) — email {DEMO_EMAIL}"
                "</body></html>"
            )

        if url.host == "web.archive.org" and url.path == "/cdx/search/cdx":
            # echo the requested target back as its own snapshot row so each
            # URL lead demonstrates one 1:1 archived copy (not one canned row)
            requested = url.params.get("url", DEMO_DOMAIN)
            return httpx.Response(200, json=[
                ["timestamp", "original", "statuscode"],
                ["20210407000000", f"https://{requested}" if "://" not in str(requested) else str(requested), "200"],
            ])

        if url.host == "cloudflare-dns.com" and url.path == "/dns-query":
            return httpx.Response(200, json={
                "Answer": [{"type": 15, "data": f"5 mail.{DEMO_DOMAIN}."}]
            })

        if url.host == "crt.sh" and url.path == "/":
            return httpx.Response(200, json=[
                {"name_value": f"cert-host.{DEMO_DOMAIN}\n*.wild.{DEMO_DOMAIN}"}
            ])

        if url.host == "www.bing.com" and url.path == "/search":
            return httpx.Response(200, text="<html>There are no results for your query</html>")

        if url.host == "www.duolingo.com" and url.path.startswith("/2017-06-30/users"):
            return httpx.Response(200, json={"users": []})

        if url.host == "ahmia.fi" and url.path == "/search/":
            return _pitch(
                '<html><body><ol class="searchResults">'
                '<li class="result">'
                "<h4><a href='/search/redirect?redirect_url=http://behindgardeghwsx3.onion/misc/demoleak'>"
                "archive dump thread mentioning demo data</a></h4>"
                "<p>demo fixture row (synthetic, fictional)</p>"
                "<cite>behindgardeghwsx3.onion</cite>"
                '<span class="lastSeen" data-timestamp="Nov. 2, 2026, 4:49 a.m.">1 day</span>'
                "</li></ol></body></html>"
            )

        # every unknown route behaves like "nothing here" — ambiguous, dropped
        return httpx.Response(404, text="")

    return httpx.MockTransport(handler)

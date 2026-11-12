"""Module: security-headers.

First-party HTTP posture probe for a URL or domain lead: one GET (the same
request a browser makes), then read the response's security headers and
report each as a literal observed fact - present or absent. Absent headers
are stated as observations ("no HSTS header"), never as exploit claims.
Hard rule #1: every row cites the response itself as evidence.

Checked: strict-transport-security, content-security-policy,
x-frame-options, x-content-type-options, referrer-policy,
permissions-policy.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "security-headers"

CHECKS = [
    ("strict-transport-security",
     "no HSTS header - downgrade/strip-TLS attacks stay possible"),
    ("content-security-policy",
     "no CSP - injected scripts run without restriction"),
    ("x-frame-options",
     "no X-Frame-Options/Framing CSP seen - clickjacking surface"),
    ("x-content-type-options",
     "no nosniff - mime confusion attacks stay possible"),
    ("referrer-policy",
     "no referrer-policy - full URLs may leak to third parties"),
    ("permissions-policy",
     "no permissions-policy - powerful browser APIs not restricted"),
]


def _canon(value: str) -> str:
    v = value.strip()
    if "://" not in v:
        v = f"https://{v}"
    return v


async def run(client: httpx.AsyncClient, target: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = _canon(target)
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    headers = {k.lower(): v.strip() for k, v in resp.headers.items()}
    evidence = str(resp.url)
    final_host = resp.url.host or url

    pivots: list[dict] = []
    missing = 0
    for name, impact in CHECKS:
        val = headers.get(name, "")
        if val:
            pivots.append({
                "type": "fact",
                "value": f"{final_host} sets {name}",
                "source": MODULE,
                "evidence_url": evidence,
                "snippet": f"{name}: {val[:80]}",
                "status": resp.status_code,
            })
        else:
            missing += 1
            pivots.append({
                "type": "fact",
                "value": f"{final_host} lacks {name}",
                "source": MODULE,
                "evidence_url": evidence,
                "snippet": f"{impact} (observed status {resp.status_code})",
                "status": resp.status_code,
            })

    pivots.append({
        "type": "fact",
        "value": f"{final_host} security headers: {len(CHECKS) - missing}/6 set",
        "source": MODULE,
        "evidence_url": evidence,
        "snippet": "posture summary from response headers only",
        "status": resp.status_code,
    })
    return {"pivots": pivots, "error": None}

"""Module 6: dns - email-domain posture (v0.3 delegates to the domains engine).

Only runs when the email domain is NOT a free mailbox provider. MX records
via DNS-over-HTTPS, certificate-transparency hostnames via crt.sh (flaky,
degraded not fatal), and the RDAP registration record. Everything is
reported as a "mail infrastructure" side list - never as identity pivots.
"""

from __future__ import annotations

import httpx

from thousand_eyes.modules import domains as domains_module

MODULE = "dns"


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"hosts": [...], "error": str|None, "skipped": str|None}.

    (Legacy email-seed entry point kept so the pipeline API is unchanged;
    the wildfire engine calls domains.run_domain directly for domain leads.)
    """
    domain = email.rsplit("@", 1)[-1].lower()

    if domains_module.is_free_mailbox(domain):
        return {"hosts": [], "error": None, "skipped": f"{MODULE}: {domain} is a free mailbox provider, skipping"}

    res = await domains_module.run_domain(client, domain)
    return {"hosts": res["hosts"], "error": "; ".join(res["errors"]) or None, "skipped": None}

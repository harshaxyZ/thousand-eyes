"""userrealm — Microsoft's own sign-in routing endpoint, keyless.

GET https://login.microsoftonline.com/common/userrealm/<email>?api-version=1.0
Verified live (2026-11-03): 200 JSON for any email, e.g.
  {"ver":"1.0","account_type":"Managed","domain_name":"live.com", ...}
  or {"account_type":"Federated","domain_name":"example.com",
      "federation_protocol":"WSTrust", ...}

This is the endpoint Azure AD itself uses to decide where a login goes —
no key, no login attempt, read-only. Findings:
  - account_type Managed + tenant domain → the mailbox lives on an M365 tenant
  - consumer domains (outlook/hotmail/live) → consumer Microsoft account
  - anything else → domain-level federation posture (infrastructure node)
One node is added: type "domain" as mail-infrastructure evidence.
"""

from __future__ import annotations

import httpx

from thousand_eyes.http_client import describe_http_error

MODULE = "userrealm"
API_URL = "https://login.microsoftonline.com/common/userrealm/{email}"
CONSUMER_TALKING = {"outlook.com", "hotmail.com", "live.com", "msn.com"}


async def run(client: httpx.AsyncClient, email: str) -> dict:
    """Returns {"pivots": [...], "error": str|None}."""
    url = API_URL.format(email=email)
    try:
        resp = await client.get(url, params={"api-version": "1.0"})
    except httpx.HTTPError as exc:
        return {"pivots": [], "error": f"{MODULE}: {describe_http_error(exc)}"}

    if resp.status_code != 200:
        return {"pivots": [], "error": f"{MODULE}: unexpected status {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"pivots": [], "error": f"{MODULE}: invalid json response ({exc})"}

    account_type = data.get("account_type") or "unknown"
    domain = data.get("domain_name") or email.rsplit("@", 1)[-1]
    tenant_id = data.get("TenantID") or data.get("tenant_id")

    snippet = f"userrealm account_type={account_type} domain_name={domain}"
    if tenant_id:
        snippet += f" tenant_id={tenant_id}"
    pivots = [{
        "type": "domain",
        "value": domain,
        "source": MODULE,
        "evidence_url": url,
        "snippet": snippet,
        "status": resp.status_code,
    }]
    return {"pivots": pivots, "error": None}

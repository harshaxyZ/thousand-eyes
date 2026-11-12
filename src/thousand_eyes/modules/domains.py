"""domains - the infrastructure arm for DOMAIN leads (v0.3).

Reworked out of dns_module so the same facts serve both an email seed
(email → its domain) and a standalone domain lead produced by e.g.
userrealm. All feeds verified keyless 2026-11-03:
  - Cloudflare DoH JSON (accept: application/dns-json) → MX records
  - crt.sh CT-log JSON → hostnames in certs   (flaky: timeout-tolerant)
  - rdap.org → registrar / event dates

Findings are "mail infrastructure" side-list entries (never identity
pivots) plus ONE new lead type (domain) echo so the graph can root from
a domain seed directly.
"""

from __future__ import annotations

import re

import httpx

from thousand_eyes.http_client import describe_http_error

_HOSTNAME_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*$")

MODULE = "domains"

FREE_MAILBOX_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com",
}

DOH_URL = "https://cloudflare-dns.com/dns-query"
CRT_SH_URL = "https://crt.sh/"
RDAP_URL = "https://rdap.org/domain/{domain}"
CRT_TIMEOUT = 25.0


def is_free_mailbox(domain: str) -> bool:
    return domain.lower() in FREE_MAILBOX_DOMAINS


async def _mx(client: httpx.AsyncClient, domain: str) -> tuple[list[dict], str | None]:
    try:
        mx_resp = await client.get(
            DOH_URL,
            headers={"accept": "application/dns-json"},
            params={"name": domain, "type": "MX"},
        )
    except httpx.HTTPError as exc:
        return [], f"{MODULE}: MX lookup failed ({describe_http_error(exc)})"

    if mx_resp.status_code != 200:
        return [], f"{MODULE}: MX lookup unexpected status {mx_resp.status_code}"
    try:
        mx_data = mx_resp.json()
    except ValueError:
        return [], f"{MODULE}: MX lookup invalid json"

    hosts = []
    for answer in mx_data.get("Answer") or []:
        if answer.get("type") != 15:  # MX
            continue
        raw_data = (answer.get("data") or "").strip()
        parts = raw_data.split(None, 1)
        target = (parts[1] if len(parts) == 2 else raw_data).rstrip(".")
        if not target:
            continue  # RFC 7505 null MX
        hosts.append({
            "type": "mail infrastructure",
            "value": target,
            "source": f"{MODULE}:mx",
            "evidence_url": f"{DOH_URL}?name={domain}&type=MX",
            "snippet": f'MX record="{raw_data}"',
        })
    return hosts, None


async def _crt_sh(client: httpx.AsyncClient, domain: str) -> tuple[list[dict], str | None]:
    try:
        crt_resp = await client.get(
            CRT_SH_URL, params={"q": domain, "output": "json"}, timeout=httpx.Timeout(CRT_TIMEOUT, connect=6.0)
        )
    except httpx.HTTPError as exc:
        # crt.sh is the pipeline's known-light failure point: degrade, don't error the scan
        return [], f"{MODULE}: crt.sh lookup failed ({describe_http_error(exc)})"
    if crt_resp.status_code != 200:
        return [], f"{MODULE}: crt.sh unexpected status {crt_resp.status_code}"
    try:
        crt_data = crt_resp.json()
    except ValueError:
        return [], f"{MODULE}: crt.sh invalid json"

    # bug fix: crt.sh returns a JSON OBJECT on its error paths; iterating a
    # dict here used to crash with an AttributeError - guard the shape.
    if not isinstance(crt_data, list):
        return [], f"{MODULE}: crt.sh returned non-list payload"

    seen: set[str] = set()
    hosts = []
    evidence = f"{CRT_SH_URL}?q={domain}&output=json"
    for entry in crt_data:
        if not isinstance(entry, dict):
            continue
        name_value = (entry.get("name_value") or "").strip()
        for name in name_value.split("\n"):
            name = name.strip().lstrip("*.").lower()
            if not name or name in seen:
                continue
            if "@" in name:
                continue  # SAN email entry, not a hostname
            if not _HOSTNAME_RE.match(name):
                continue  # CA issuer label etc.
            if not (name == domain or name.endswith("." + domain)):
                continue  # crt.sh fuzzy matches unrelated certs
            seen.add(name)
            hosts.append({
                "type": "mail infrastructure",
                "value": name,
                "source": f"{MODULE}:crt.sh",
                "evidence_url": evidence,
                "snippet": f'certificate name="{name}"',
            })
    return hosts, None


async def _rdap(client: httpx.AsyncClient, domain: str) -> dict | None:
    url = RDAP_URL.format(domain=domain)
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    registrar = ""
    for entity in data.get("entities") or []:
        for role in entity.get("roles") or []:
            if role == "registrar":
                vcard = (entity.get("vcardArray") or [None, []])[1]
                for field in vcard if isinstance(vcard, list) else []:
                    if field and field[0] == "fn":
                        registrar = str(field[3])
                        break
    events = {e.get("eventAction"): (e.get("eventDate") or "")[:10] for e in data.get("events") or []}
    snippet = "domain registration record"
    if registrar:
        snippet += f" (registrar {registrar})"
    registered = events.get("registration")
    if registered:
        snippet += f", registered {registered}"
    return {
        "type": "mail infrastructure",
        "value": domain,
        "source": f"{MODULE}:rdap",
        "evidence_url": url,
        "snippet": snippet,
    }


async def run_domain(client: httpx.AsyncClient, domain: str) -> dict:
    """Returns {"hosts": [...], "leaves_meta": [...], "error": str|None}."""
    domain = domain.strip().lower().rstrip(".")
    errors: list[str] = []
    hosts: list[dict] = []

    if not _HOSTNAME_RE.match(domain):
        return {"hosts": [], "errors": [f"{MODULE}: {domain} is not a plausible hostname"], "error": None}

    mx_hosts, err = await _mx(client, domain)
    if err:
        errors.append(err)
    hosts += mx_hosts

    if not is_free_mailbox(domain):
        crt_hosts, err2 = await _crt_sh(client, domain)
        if err2:
            errors.append(err2)
        hosts += crt_hosts

    rdap_host = await _rdap(client, domain)
    if rdap_host:
        hosts.append(rdap_host)

    return {"hosts": hosts, "errors": errors, "error": None}

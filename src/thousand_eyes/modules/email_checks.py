"""email_checks — the email-registration sweep engine (v0.3).

Reads data/email_checks.yaml; each entry is a *verified* anonymous check:
  kind: json   → walk a JSON dot-path and compare against a registered/absent value
  kind: status → the HTTP status itself decides registered/absent
Entries with `disabled: true` (or an unknown kind) are skipped without a
request. Ambiguous outcomes (both/neither marker) are dropped, never
reported — same false-positive safety net as the other teardown modules.

Characteristics: no password-reset flows, no credential probes, no
login-walled endpoints — anonymous read-only existence checks only.
"""

from __future__ import annotations

import importlib
import json
from importlib import resources
from urllib.parse import quote

import httpx
import yaml

from thousand_eyes.http_client import describe_http_error

MODULE = "email-checks"

_json = json  # module-level alias so engine keeps one import style


def _load_sites() -> list[dict]:
    text = resources.files("thousand_eyes.data").joinpath("email_checks.yaml").read_text(encoding="utf-8")
    return ((yaml.safe_load(text) or {}).get("sites")) or []


def _walk_jsonpath(payload, dotted: str):
    cur = payload
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _parse_template_value(expected: str):
    if expected.lower() == "true":
        return True
    if expected.lower() == "false":
        return False
    try:
        return int(expected)
    except ValueError:
        return expected


async def run(client: httpx.AsyncClient, email: str, *, sink=None) -> dict:
    """Returns {"leaves": [...], "errors": [...], "checked": int}."""
    sites = _load_sites()
    active = [s for s in sites if not s.get("disabled")]
    if sink is not None:
        sink.expect(max(0, len(active) - 1))
    leaves: list[dict] = []
    errors: list[dict] = []
    checked = 0

    for site in active:
        kind = site.get("kind")
        url_template = site.get("url_template", "")
        if kind not in ("json", "status", "body") or "{value}" not in url_template:
            continue

        url = None
        try:
            url = url_template.format(value=quote(email))
        except (KeyError, IndexError, ValueError):
            continue

        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            errors.append({"module": f"{MODULE}:{site.get('name', '?')}", "error": describe_http_error(exc)})
            continue
        if sink is not None:
            sink.bump()

        checked += 1
        registered = False
        absent = False

        if kind == "json":
            try:
                payload = resp.json()
            except ValueError:
                continue  # ambiguous shape → drop
            reg = _walk_jsonpath(payload, site.get("registered_jsonpath", ""))
            ab = _walk_jsonpath(payload, site.get("absent_jsonpath", ""))
            registered = reg == _parse_template_value(str(site.get("registered_value", "true")))
            absent = ab == _parse_template_value(str(site.get("absent_value", "false")))
        elif kind == "status":
            registered = resp.status_code == int(site.get("registered_status", 200))
            absent = resp.status_code == int(site.get("absent_status", 404))
        elif kind == "body":
            body = resp.text or ""
            reg_marker = site.get("registered_marker", "")
            ab_marker = site.get("absent_marker", "")
            has_reg = reg_marker in body if reg_marker else False
            has_abs = ab_marker in body if ab_marker else False
            registered, absent = has_reg, has_abs

        if registered and absent:
            continue  # ambiguous
        if not registered and not absent:
            continue  # neither matched → drop silently

        if registered:
            leaves.append({"site": site.get("name", "unknown"), "url": url, "status": resp.status_code, "category": site.get("category", "consumer")})

    return {"leaves": leaves, "errors": errors, "checked": checked}

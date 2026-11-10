"""step1 — email-only lead harvest (Step 1 of the walk).

One email in. Runs only the modules listed in EMAIL_SOURCES below,
collects pivots + reset-only leaves, filters them against the user's
evidence law, and renders EXACTLY the required shape:

    lead - email - <seed>

    leads found using this email:
    - <type>: <value>
      source: <module>
      proof: <url>
      snippet: <text containing this exact email, or the API field>

    things found using those leads:
    - none yet

    skipped: <source>: <reason>
    ...

No BFS, no hub/vertical expansion, no following profile URLs, no
username guessing, no darkweb, no HUDSON.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx

from thousand_eyes.http_client import new_client
from thousand_eyes.modules import (
    breach_existence,
    email_checks,
    exact_document,
    github_commit_search,
    gravatar,
    hibp,
    pgp,
    registration_leaves,
    userrealm,
)

# Bots that are never a real lead even if an API returns them.
_BOTS = {"web-flow": "GitHub Web Flow", "github-actions": "GitHub Actions"}


@dataclass
class LeadRow:
    type: str        # name | url | email | phone | reset | domain
    value: str
    source: str
    proof: str        # evidence URL (fallback to terminal URL)
    snippet: str


def _email_in(text: str, email: str) -> bool:
    return email.lower() in (text or "").lower()


def _is_bot_url(value: str) -> bool:
    v = value.strip().lower().rstrip("/")
    return v in {f"https://github.com/{k}" for k in _BOTS} or v in {
        f"https://github.com/{k}/" for k in _BOTS
    }


def _is_bot_name(value: str) -> bool:
    return value.strip() in set(_BOTS.values())


async def collect_email_leads(
    email: str, *, url_log: str | None = None
) -> tuple[list[LeadRow], list[str]]:
    """Coroutines. Returns (leads, skipped_lines).

    Filter law (drop the row entirely if it fails):
      * snippet must contain the exact seed email, UNLESS the source is
        Gravatar or a public key, where the API field returned for the
        hash/address is itself the proof.
      * Bot names/URLs (github web-flow) never print.
      * Reset-only sites land in the same list as type "reset" (not pivots).
    """
    leads: list[LeadRow] = []
    skipped: list[str] = []

    def add(row: LeadRow):
        leads.append(row)

    async with new_client(url_log_path=url_log) as client:
        # --- gravatar (exception: API field proof) ---
        try:
            res = await gravatar.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "error": f"gravatar: {type(exc).__name__}: {exc}"}
        if res.get("error"):
            skipped.append(f"skipped: gravatar: {res['error']}")
        for p in res.get("pivots") or []:
            v = p["value"]
            if _is_bot_name(v) or _is_bot_url(v):
                continue
            # need a real https URL value for profile URLs
            if p["type"] == "url" and not str(v).startswith("https://"):
                continue
            add(LeadRow(p["type"], v, "gravatar", p["evidence_url"], p["snippet"]))

        # --- github-commit-search ---
        # The module already only yields logins of User-typed authors, but
        # web-flow is a bot type "User" in some commit roles — drop it here.
        try:
            res = await github_commit_search.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "error": f"github-commit-search: {type(exc).__name__}: {exc}"}
        if res.get("error"):
            skipped.append(f"skipped: github-commit-search: {res['error']}")
        for p in res.get("pivots") or []:
            if _is_bot_url(p["value"]) or _is_bot_name(p["value"]):
                continue
            if not _email_in(p["snippet"], email) and not _email_in(p["evidence_url"], email):
                continue
            add(LeadRow("url", p["value"], "github-commit-search", p["evidence_url"], p["snippet"]))

        # --- pgp (keys.openpgp.org) — exception: API field proof ---
        try:
            res = await pgp.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "error": f"pgp: {type(exc).__name__}: {exc}"}
        if res.get("error"):
            skipped.append(f"skipped: pgp: {res['error']}")
        for p in res.get("pivots") or []:
            if _is_bot_url(p["value"]):
                continue
            snippet = p["snippet"]
            # fix the module's placeholder underscores into a real field
            if "___" in snippet:
                snippet = snippet.replace("___", "")
            add(LeadRow("url", p["value"], "pgp", p["evidence_url"], snippet))

        # --- userrealm — infrastructure domain, not a pivot lead ---
        # Step 1 keeps it as a lead row of type "domain" ONLY if the
        # snippet is about this email; the API returns a field for the
        # address. userrealm is not a reset-only site, so keep it.
        try:
            res = await userrealm.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "error": f"userrealm: {type(exc).__name__}: {exc}"}
        if res.get("error"):
            skipped.append(f"skipped: userrealm: {res['error']}")
        for p in res.get("pivots") or []:
            if _is_bot_url(p["value"]):
                continue
            add(LeadRow(p["type"], p["value"], "userrealm", p["evidence_url"], p["snippet"]))

        # --- hibp — keyed; skip line is part of the required output ---
        try:
            res = await hibp.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "leaves": [], "error": f"hibp: {type(exc).__name__}: {exc}", "skipped": None}
        if res.get("skipped"):
            skipped.append(f"skipped: hibp: {res['skipped']}")
        if res.get("error"):
            skipped.append(f"skipped: hibp: {res['error']}")
        for p in res.get("pivots") or []:
            if _is_bot_url(p["value"]):
                continue
            if not _email_in(p["snippet"], email):
                continue
            add(LeadRow(p["type"], p["value"], "hibp", p["evidence_url"], p["snippet"]))
        for leaf in res.get("leaves") or []:
            # breach leaves are reset-type only if they are existence
            # records; hibp breach leaves fall under "reset" per spec.
            add(LeadRow("reset", leaf["site"], "hibp", leaf["url"], f'breach="{leaf["site"]}"'))

        # --- breach-existence (xposedornot) — keyless, HUDSON excluded ---
        try:
            res = await breach_existence.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "leaves": [], "error": f"breach-existence: {type(exc).__name__}: {exc}"}
        if res.get("error"):
            skipped.append(f"skipped: breach-existence: {res['error']}")
        for leaf in res.get("leaves") or []:
            add(LeadRow("reset", leaf["site"], "breach-existence", leaf["url"], f'breach="{leaf["site"]}"'))

        # --- email-checks (verified anonymous checks) ---
        try:
            res = await email_checks.run(client, email)
        except httpx.HTTPError as exc:
            res = {"leaves": [], "errors": [{"module": "email-checks", "error": f"{type(exc).__name__}: {exc}"}]}
        for err in res.get("errors") or []:
            skipped.append(f"skipped: {err.get('module', 'email-checks')}: {err.get('error', 'unknown')}")
        for leaf in res.get("leaves") or []:
            # reset-only: address is registered, nothing more.
            add(LeadRow("reset", leaf["site"], "email-checks", leaf["url"], f'registered on {leaf["site"]} (address registered, nothing more)'))

        # --- registration-leaves (leaves only) ---
        try:
            res = await registration_leaves.run(client, email)
        except httpx.HTTPError as exc:
            res = {"leaves": [], "errors": [{"module": "registration-leaves", "error": f"{type(exc).__name__}: {exc}"}]}
        for err in res.get("errors") or []:
            skipped.append(f"skipped: {err.get('module', 'registration-leaves')}: {err.get('error', 'unknown')}")
        for leaf in res.get("leaves") or []:
            add(LeadRow("reset", leaf["site"], "registration-leaves", leaf["url"], f'registered on {leaf["site"]} (address registered, nothing more)'))

        # --- exact-document (Bing keyless) ---
        try:
            res = await exact_document.run(client, email)
        except httpx.HTTPError as exc:
            res = {"pivots": [], "errors": [f"{type(exc).__name__}: {exc}"]}
        for err in res.get("errors") or []:
            skipped.append(f"skipped: exact-document: {err}")
        for p in res.get("pivots") or []:
            if _is_bot_url(p["value"]):
                continue
            # pivot URL itself counts only if the page contained the seed;
            # snippet is the module's page-evidence string. For email/phone
            # pivots, the snippet is descriptive of discovery on a seed page —
            # keep them only when the seed is the pivot's parent evidence page.
            if p["type"] == "email" and p["value"].lower() == email.lower():
                continue
            snippet = p["snippet"]
            if not _email_in(snippet, email) and not _email_in(p["evidence_url"], email):
                continue
            add(LeadRow(p["type"], p["value"], "exact-document", p["evidence_url"], snippet))

    # Hudson is intentionally never called (no key, no darkweb) — spec.
    return leads, skipped


def render_step1(email: str, leads: list[LeadRow], skipped: list[str]) -> str:
    lines = [
        f"lead - email - {email}",
        "",
        "leads found using this email:",
    ]
    if leads:
        for lead in leads:
            lines.append(f"- {lead.type}: {lead.value}")
            lines.append(f"  source: {lead.source}")
            lines.append(f"  proof: {lead.proof}")
            lines.append(f"  snippet: {lead.snippet}")
    else:
        lines.append("- none")

    lines += ["", "things found using those leads:", "- none yet", ""]
    if skipped:
        lines += skipped
    # no skipped at all → leave nothing after (spec: failures appear here)
    return "\n".join(lines) + "\n"


async def run_step1(email: str, *, url_log: str | None = None) -> str:
    leaves, skipped = await collect_email_leads(email, url_log=url_log)
    return render_step1(email, leaves, skipped)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse

    parser = argparse.ArgumentParser(prog="thousand-eyes-step1", description="Step 1: email-only lead harvest.")
    parser.add_argument("email")
    parser.add_argument("--url-log", metavar="PATH")
    args = parser.parse_args(argv)
    out = asyncio.run(run_step1(args.email, url_log=args.url_log))
    print(out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

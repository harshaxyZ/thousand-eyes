"""Lead classification + tool routing (deterministic, no LLM needed)."""

from __future__ import annotations

import re
from typing import List

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
URL_RE = re.compile(r"^https?://", re.I)
DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 .-]{6,14}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")


def classify(lead: str) -> str:
    v = lead.strip()
    if EMAIL_RE.match(v):
        return "email"
    if URL_RE.match(v):
        return "url"
    if PHONE_RE.match(v) and any(ch.isdigit() for ch in v):
        return "phone"
    if DOMAIN_RE.match(v) and "." in v and " " not in v and v.count(".") <= 3:
        return "domain"
    if HANDLE_RE.match(v):
        return "username"
    return "name"


def tools_for(kind: str) -> List[str]:
    """First-round tool families for a classified lead."""
    if kind == "email":
        return ["engine.hibp", "engine.xposed", "engine.hudsonrock", "engine.gravatar",
                "engine.pgp", "engine.userrealm", "engine.dns", "engine.email-checks",
                "engine.registration-leaves", "engine.github-commits",
                "engine.exact-document", "dork.email", "dark.ahmia-index",
                "dark.paste-index"]
    if kind == "username":
        return ["engine.username-sweep", "engine.github-profile", "engine.reddit",
                "engine.keybase", "engine.exact-document", "dork.username",
                "dark.ahmia-index", "dark.paste-index"]
    if kind == "domain":
        return ["engine.domain", "engine.dns", "engine.wayback", "dork.domain",
                "engine.exact-document"]
    if kind == "url":
        return ["engine.fetch", "engine.wayback", "engine.exact-document", "dork.url"]
    if kind == "phone":
        return ["engine.exact-document", "dork.email"]
    return ["engine.exact-document", "dork.email"]


def pivot_kind(value: str) -> str:
    return classify(value)

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


# Agent-only tools fired next to the registry modules for a lead kind. The
# engine.* and dork.* lists are never written here: they come from
# registry.py, so a new source reaches the agent the moment it is registered.
# (engine.darkweb already queries the ahmia index, so dark.ahmia-index is
# left to the self-audit instead of being fired twice.)
_EXTRA_TOOLS = {
    "email": ["dark.paste-index"],
    "username": ["dark.paste-index"],
}

# kinds no registry module answers still get the exact-phrase search
_FALLBACK_TOOLS = ["engine.exact-document"]


def tools_for(kind: str) -> List[str]:
    """Tools fired for a classified lead: every registry module that answers
    this lead type (dork sets included), plus the agent-only paste tool."""
    from thousand_eyes import registry

    names = [spec.tool_name for spec in registry.modules_for(kind)]
    names += _EXTRA_TOOLS.get(kind, [])
    return list(dict.fromkeys(names)) or list(_FALLBACK_TOOLS)


def pivot_kind(value: str) -> str:
    return classify(value)

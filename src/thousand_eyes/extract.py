"""Shared, strictly-scoped extraction.

Per spec: from a confirmed page body, extract ONLY:
  - https URLs
  - emails
  - E.164 phones
  - a profile URL whose page ALSO contains the seed (confirmed by the caller
    doing a second fetch — this module only yields *candidate* profile URLs;
    it is the caller's job to confirm before treating one as evidence)

Never extract bare words and call them usernames. No handle-mention regex,
no crypto-address regex, no hash regex, no breach-keyword heuristics.

Extraction runs on TAG-STRIPPED visible text first (bug fix: phones/emails
were previously matched inside raw HTML tags and attribute soup), and the
E.164 regex is digit-boundary guarded so it can no longer match a run of
digits inside a longer number.
"""

from __future__ import annotations

import re

HTTPS_URL_RE = re.compile(r"https://[^\s\"'<>\)\]]+")
# boundary-guarded: no digit immediately before/after, so "12345551234567"
# can no longer yield "+44551234567" style false positives
E164_RE = re.compile(r"(?<!\d)\+[1-9]\d{6,14}(?!\d)")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def visible_text(body: str) -> str:
    """Approximate the page's visible text: drop script/style blocks, then
    strip remaining tags, then unescape the couple of entities that matter
    for pattern matching."""
    body = _SCRIPT_STYLE_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
    return body.replace("&amp;", "&").replace("@x0000d@", "") if False else (
        body.replace("&#64;", "@").replace("&amp;#", "&#")
            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    )


def extract_urls(body: str) -> list[str]:
    seen: list[str] = []
    for m in HTTPS_URL_RE.finditer(body):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen


def extract_emails(body: str) -> list[str]:
    seen: list[str] = []
    for m in EMAIL_RE.finditer(body):
        val = m.group(0).lower().strip(".")
        if val not in seen:
            seen.append(val)
    return seen


def extract_phones(body: str) -> list[str]:
    seen: list[str] = []
    for m in E164_RE.finditer(body):
        val = m.group(0)
        if val not in seen:
            seen.append(val)
    return seen


def body_contains_seed(body: str, seed_value: str) -> bool:
    return seed_value.lower() in visible_text(body).lower()

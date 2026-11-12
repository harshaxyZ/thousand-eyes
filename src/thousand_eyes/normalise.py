"""Module 1: normalise.

Classifies lead input. Accepted types:
  - email (lowercased; plus-tag stripped ONLY when --fold-plus is passed)
  - E.164 phone
  - https:// URL
  - username - USER-ASSERTED lead (the operator typed it; that is the
    evidence chain: it was never derived from something else by us)

v0.3 accepts multiple leads per run; this module stays per-lead. It never
derives a username, real name, or phone from an email local-part on its own
- username candidates are only created by explicit user input, or by the
opt-in --wild permutation mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_HTTPS_URL_RE = re.compile(r"^https://[^\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


class InvalidSeed(ValueError):
    pass


@dataclass
class Seed:
    type: str  # "email" | "phone" | "url" | "username"
    value: str


def normalise_seed(raw: str, *, fold_plus: bool = False) -> Seed:
    raw = raw.strip()
    if not raw:
        raise InvalidSeed("empty input")

    if _EMAIL_RE.match(raw):
        local, _, domain = raw.partition("@")
        local = local.lower()
        domain = domain.lower()
        if fold_plus and "+" in local:
            local = local.split("+", 1)[0]
        return Seed(type="email", value=f"{local}@{domain}")

    if _E164_RE.match(raw):
        return Seed(type="phone", value=raw)

    if _HTTPS_URL_RE.match(raw):
        return Seed(type="url", value=raw)

    # username gate: any bare handle the operator typed deliberately.
    # URLs/emails/phones are all matched above, so what falls through here to
    # the username regex is exactly "a word that looks like a handle" - that
    # is user-asserted input, not a derivation.
    if _USERNAME_RE.match(raw) and not raw.isdigit():
        return Seed(type="username", value=raw)

    raise InvalidSeed(
        f"'{raw}' is not an email, an E.164 phone (+15551234567), an https:// URL, or a username"
    )

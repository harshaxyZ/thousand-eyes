"""impact.py — v0.4 "hacker's triage".

Every graph finding is ranked into one of three impact tiers before it is
shown. The rank answers one question: *if someone knew this, what could they
actually do with it?*

  tier 3  WEAPONISED  — identity keys: real name, second email, live phone,
                        stealer-log hits (credential theft victims list).
  tier 2  PIVOTABLE   — accounts on real platforms. Knowing the handle, an
                        attacker can visit the profile, harvest the bio /
                        contact info, and pivot to the next platform. This
                        is the hacker POV you asked about: a Pinterest or
                        Spotify account is not noise — it is a doorway.
  tier 1  AMBIENT     — footprints that confirm activity but give an
                        attacker little: archive snapshots, mail infra,
                        generic domain posture, misc indexed pages.

Output law: every tier-3 and tier-2 line is a RESULT (what the attacker
sees), never a fetch description. The how-it-was-found machinery lives in
--verbose and the JSON.

The rank is presentation-only. It never adds, removes or rewrites evidence;
a node whose rank is unknown still renders (unranked = shown in ambient).
"""

from __future__ import annotations

from urllib.parse import urlparse

# tier constants
WEAPONISED = 3
PIVOTABLE = 2
AMBIENT = 1
UNRANKED = 0

TIER_LABEL = {
    WEAPONISED: "weaponised",
    PIVOTABLE: "pivotable",
    AMBIENT: "ambient",
}

# Node types that are identity keys by themselves.
_TYPE_BASE = {
    "name": WEAPONISED,       # real names shared by canonical identifiers
    "email": WEAPONISED,      # a second email is a key to other accounts
    "phone": WEAPONISED,      # E.164 numbers are SMS-attack surfaces
    "handle": PIVOTABLE,      # breach leakage of a username
    "domain": AMBIENT,        # posture/infrastructure by default
    "url": None,              # classified by platform below
    "username": None,         # seed typed by operator; classified by host
}

# Hostname → tier for url nodes + leaf site names. Curated from the WMN
# categories and the manual-check list: sites where a live profile page is
# a real pivot opportunity (bio, email, phone, social graph) outrank sites
# where the profile is anonymous card-content.
_HIGH_VALUE_HOSTS: dict[str, str] = {
    "github.com": "code identity — commit emails, repos, orgs",
    "keybase.io": "keybase — proves cross-platform identity",
    "www.reddit.com": "reddit — account history, social graph",
    "reddit.com": "reddit — account history, social graph",
    "twitter.com": "X/Twitter — public account",
    "x.com": "X/Twitter — public account",
    "mobile.twitter.com": "X/Twitter — public account",
    "www.instagram.com": "instagram — bio, contact linking",
    "instagram.com": "instagram — bio, contact linking",
    "www.pinterest.com": "pinterest — bio + linked accounts",
    "pinterest.com": "pinterest — bio + linked accounts",
    "open.spotify.com": "spotify — listening identity, connections",
    "soundcloud.com": "soundcloud — public identity",
    "www.twitch.tv": "twitch — identity + streaming life",
    "twitch.tv": "twitch — identity + streaming life",
    "www.tiktok.com": "tiktok — public identity",
    "bsky.app": "bluesky — DID-resolvable public account",
    "mastodon.social": "mastodon — fediverse identity",
    "medium.com": "medium — writing identity",
    "www.youtube.com": "youtube — channel identity",
    "youtube.com": "youtube — channel identity",
    "about.me": "about.me — self-authored identity card",
    "www.wattpad.com": "wattpad — author identity",
    "www.goodreads.com": "goodreads — reading identity",
    "www.roblox.com": "roblox — young-audience identity",
    "www.flickr.com": "flickr — photo identity (EXIF-era legacy)",
    "news.ycombinator.com": "hackernews — long-lived pseudonymous history",
    "www.paypal.com": "paypal — money rails (existence signal)",
    "cash.app": "money rails (existence signal)",
    "venmo.com": "money rails (public payment feed)",
}

_LOW_VALUE_HOST_PREFIXES = (
    "web.archive.org",
    "archive.org",
    "cachedview",
    "localhost",
)

# Leaf site label prefixes → tier/classification for leaf rows.
_LEAF_RULES: tuple[tuple[str, int, str], ...] = (
    ("stealer log", WEAPONISED, "infostealer log entry — machine shows stored credentials for this identity"),
    ("breach", PIVOTABLE, "breach exposure — attacker knows this account exists in a dump"),
    ("darknet", AMBIENT, "darknet-indexed mention"),
    ("mail inf", AMBIENT, "mail infrastructure"),
)


def classify_host(url_or_host: str) -> int:
    """Host-based tier for a url node."""
    host = ""
    if "://" in url_or_host:
        try:
            host = (urlparse(url_or_host).hostname or "").lower()
        except ValueError:
            host = ""
    else:
        host = url_or_host.lower()
    if not host:
        return AMBIENT
    if any(host == p or host.startswith(p + ".") for p in _LOW_VALUE_HOST_PREFIXES):
        return AMBIENT
    if host in _HIGH_VALUE_HOSTS:
        return PIVOTABLE
    return AMBIENT


def pivot_tier(node_type: str, value: str, category: str = "", label: str = "") -> tuple[int, str]:
    """(tier, why) for a pivot node."""
    base = _TYPE_BASE.get(node_type)
    if base is not None:
        return base, _TYPE_WHY(node_type)
    # url / username → platform classification, category hint for label
    site = label or category
    tier = classify_host(value)
    if tier == PIVOTABLE:
        host_key = urlparse(value).hostname or "" if "://" in value else value.lower()
        why = _HIGH_VALUE_HOSTS.get(host_key.lower(), "live profile page — a pivot doorway")
        if not why.endswith("doorway"):
            return PIVOTABLE, why
        return PIVOTABLE, f"{site or 'platform'} profile — attacker can harvest bio/links from here"
    return AMBIENT, "secondary footprint"


def _TYPE_WHY(node_type: str) -> str:
    return {
        "name": "real name — the master key for people-search and docs",
        "email": "second email — direct pivot into other accounts",
        "phone": "phone number — SIM-swap/OSSINT surface",
        "handle": "username leaked in an adjacent record",
        "domain": "mail/tenant posture observed",
    }[node_type]


def leaf_tier(site_label: str, category: str = "") -> tuple[int, str]:
    """(tier, why) for a leaf row (registered/breach/darknet/infra)."""
    low = site_label.lower()
    for prefix, tier, why in _LEAF_RULES:
        if low.startswith(prefix):
            return tier, why
    if category == "breach" or "breach" in low:
        return PIVOTABLE, "breach exposure — the attacker has this account in a dump"
    if category == "darknet":
        return AMBIENT, "darknet-indexed mention"
    # consumer registration check (duolingo-style account-exists leaf)
    return PIVOTABLE, "account exists — attacker can attempt resets/contact on this service"


def rank_url_like(value: str) -> str:
    """Short one-line 'what an attacker does with this' string for a url."""
    host = urlparse(value).hostname if "://" in value else ""
    return _HIGH_VALUE_HOSTS.get((host or "").lower(), "")

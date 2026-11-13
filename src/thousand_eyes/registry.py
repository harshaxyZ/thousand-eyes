"""The API bridge.

One registry that declares every free, first-party endpoint the tool knows
how to call, and which lead types each one answers. Almost all of them are
keyless; the one that needs the operator's own key (HIBP) reports `skipped`
without it, never a clean negative. Both the
deterministic engine (scan.py) and the LLM agent tool layer (agent/tools.py)
are GENERATED from this file - so a new source is added exactly once and
works in every path.

Rules baked into this file:
  * every entry is a first-party public endpoint of the platform itself
    (no third-party scrapers, no paid APIs, no shady tooling);
  * every module returns the same shape:
        {"pivots": [{"type","value","source","evidence_url","snippet","status"}],
         "leaves": [{"site","url","status","category"}],
         "error": str|None, "skipped": str|None}
  * a pivot only exists because the response body confirmed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from thousand_eyes.modules import (
    archive_creator,
    bluesky_profile,
    breach_existence,
    chess_profile,
    codeberg_profile,
    darkweb,
    devto_profile,
    discogs_profile,
    dockerhub_profile,
    dns_module,
    domains,
    dorks,
    email_checks,
    exact_document,
    fetch,
    github_commit_search,
    github_profile,
    gitlab_profile,
    gravatar,
    hackernews_user,
    hibp,
    keybase_proofs,
    leakcheck,
    lichess_profile,
    mastodon_lookup,
    pgp,
    reddit_user,
    registration_leaves,
    security_headers,
    username_checks,
    userrealm,
    wayback,
    wikipedia_user,
)


@dataclass
class Ctx:
    """Per-run context handed to every module."""
    sink: Any = None
    all_sites: bool = False
    budget: float = 0.0
    tor_proxy: str = ""     # --tor: query the dark-web index over Tor too


ModuleFn = Callable[[httpx.AsyncClient, str, Ctx], Awaitable[dict]]


@dataclass(frozen=True)
class ModuleSpec:
    """One callable source."""
    label: str                     # report label, e.g. "github-profile"
    lead_types: tuple[str, ...]    # which lead types feed it
    fn: ModuleFn
    agent_name: str = ""           # name in the agent tool registry
    desc: str = ""
    inputs: tuple[str, ...] = ()
    self_tallying: bool = False    # ticks the progress bar internally
    max_depth: int | None = None   # only run for leads at/below this depth

    @property
    def tool_name(self) -> str:
        return self.agent_name or self.label


# ------------------------------------------------------------------ adapters


def _simple(fn):
    """Wrap a plain module fn (client, value) -> dict."""
    async def _run(client: httpx.AsyncClient, value: str, ctx: Ctx) -> dict:
        return await fn(client, value)
    return _run


def _with_sink(fn, *, all_sites: bool = False):
    async def _run(client: httpx.AsyncClient, value: str, ctx: Ctx) -> dict:
        kwargs = {"sink": ctx.sink}
        if all_sites:
            kwargs["all_sites"] = ctx.all_sites
        return await fn(client, value, **kwargs)
    return _run


async def _email_domain_posture(client: httpx.AsyncClient, email: str, ctx: Ctx) -> dict:
    """Mail infrastructure for the email's own domain (free mailboxes are
    skipped - probing gmail.com's posture says nothing about the person)."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domains.is_free_mailbox(domain):
        return {"pivots": [], "leaves": [],
                "skipped": f"{domain} is a free mailbox provider, domain arm skipped"}
    res = await domains.run_domain(client, domain)
    leaves = [{"site": f"mail infrastructure: {h['value']}",
               "url": h.get("evidence_url") or "", "status": None,
               "category": "infrastructure"}
              for h in res.get("hosts") or []]
    return {"pivots": [], "leaves": leaves,
            "errors": [str(e) for e in res.get("errors") or []]}


async def _domain_posture(client: httpx.AsyncClient, domain: str, ctx: Ctx) -> dict:
    """DoH MX + certificate transparency + RDAP posture for a domain lead."""
    res = await domains.run_domain(client, domain)
    leaves = [{"site": f"mail infrastructure: {h['value']}",
               "url": h.get("evidence_url") or "", "status": None,
               "category": "infrastructure"}
              for h in res.get("hosts") or []]
    return {"pivots": [], "leaves": leaves,
            "errors": [str(e) for e in res.get("errors") or []]}


async def _darkweb_note(client: httpx.AsyncClient, term: str, ctx: Ctx) -> dict:
    res = await darkweb.run(client, term, sink=ctx.sink, tor_proxy=ctx.tor_proxy)
    if res.get("note"):
        res = dict(res)
        res["skipped"] = str(res["note"])
    return res


def _dorks(kind: str):
    """web-dorks adapter: the dork set depends on the lead type."""
    async def _run(client: httpx.AsyncClient, value: str, ctx: Ctx) -> dict:
        return await dorks.run(client, value, kind)
    return _run


_DORK_DESC = ("Search-engine dork set for {a} {kind}: paste sites, exposed "
              "documents, code hosts (Google when a key is set, else Bing/DDG/Mojeek)")


def _dork_spec(kind: str) -> "ModuleSpec":
    # seeds only (max_depth=0): search engines rate-limit fast, and a dork
    # set per spawned lead would burn the whole budget on block pages
    article = "an" if kind[0] in "aeiou" else "a"
    return ModuleSpec("web-dorks", (kind,), _dorks(kind), f"dork.{kind}",
                      _DORK_DESC.format(a=article, kind=kind), (kind,), max_depth=0)


async def _breach_existence(client: httpx.AsyncClient, email: str, ctx: Ctx) -> dict:
    return await breach_existence.run(client, email)


async def _hudsonrock(client: httpx.AsyncClient, email: str, ctx: Ctx) -> dict:
    return await breach_existence.run_hudsonrock(client, email)


# ------------------------------------------------------------------ registry

MODULES: list[ModuleSpec] = [
    # ---------------------------------------------------------- email leads
    ModuleSpec("gravatar", ("email",), _simple(gravatar.run),
               "engine.gravatar", "Gravatar profile + hash exposure for an email",
               ("email",)),
    ModuleSpec("hibp", ("email",), _simple(hibp.run),
               "engine.hibp", "Have I Been Pwned breach existence for an email (needs HIBP_API_KEY)",
               ("email",)),
    ModuleSpec("breach-existence", ("email",), _breach_existence,
               "engine.xposed", "XposedOrNot breach existence for an email", ("email",)),
    ModuleSpec("hudsonrock", ("email",), _hudsonrock,
               "engine.hudsonrock", "Hudson Rock infostealer-log exposure for an email (keyless)",
               ("email",)),
    ModuleSpec("leakcheck", ("email",), _simple(leakcheck.run),
               "engine.leakcheck", "LeakCheck public index: which breaches list an email, "
               "and which field types leaked (keyless, names only)", ("email",)),
    ModuleSpec("github-commit-search", ("email",), _simple(github_commit_search.run),
               "engine.github-commits", "GitHub commit search by email (public code)",
               ("email",)),
    ModuleSpec("userrealm", ("email",), _simple(userrealm.run),
               "engine.userrealm", "Microsoft userrealm account lookup for an email",
               ("email",)),
    ModuleSpec("pgp", ("email",), _simple(pgp.run),
               "engine.pgp", "PGP key existence for an email (keyservers)", ("email",)),
    ModuleSpec("email-checks", ("email",), _with_sink(email_checks.run),
               "engine.email-checks", "Consumer registration sweep for an email",
               ("email",), self_tallying=True),
    ModuleSpec("registration-leaves", ("email",), _with_sink(registration_leaves.run),
               "engine.registration-leaves", "Account-registration leaves for an email",
               ("email",), self_tallying=True),
    ModuleSpec("email-domain-posture", ("email",), _email_domain_posture,
               "engine.mail-posture", "Mail infrastructure posture for the email's domain",
               ("email",)),
    ModuleSpec("exact-document", ("email", "phone", "username"), _simple(exact_document.run),
               "engine.exact-document", "Exact-document web search for the identifier",
               ("email", "phone", "username")),
    ModuleSpec("darkweb", ("email", "username", "domain"), _darkweb_note,
               "engine.darkweb", "Darknet index mentions (ahmia clearnet index only)",
               ("email", "username", "domain"), self_tallying=True),

    # ------------------------------------------- search dorks (every type)
    _dork_spec("email"),
    _dork_spec("username"),
    _dork_spec("domain"),
    _dork_spec("phone"),
    _dork_spec("url"),

    # ------------------------------------------------------- username leads
    ModuleSpec("username-sweep", ("username",),
               _with_sink(username_checks.run, all_sites=True),
               "engine.username-sweep", "WhatsMyName multi-site username sweep",
               ("username",), self_tallying=True),
    ModuleSpec("github-profile", ("username",), _simple(github_profile.run),
               "engine.github-profile", "GitHub profile existence + metadata for a username",
               ("username",)),
    ModuleSpec("gitlab-profile", ("username",), _simple(gitlab_profile.run),
               "engine.gitlab", "GitLab profile existence + metadata for a username",
               ("username",)),
    ModuleSpec("keybase-proofs", ("username",), _simple(keybase_proofs.run),
               "engine.keybase", "Keybase signed-proof discovery for a username",
               ("username",)),
    ModuleSpec("reddit-user", ("username",), _simple(reddit_user.run),
               "engine.reddit", "Reddit account + activity presence for a username",
               ("username",)),
    ModuleSpec("mastodon-lookup", ("username",), _simple(mastodon_lookup.run),
               "engine.mastodon", "Mastodon (mastodon.social) account lookup for a username",
               ("username",)),
    ModuleSpec("bluesky-profile", ("username",), _simple(bluesky_profile.run),
               "engine.bluesky", "Bluesky AppView profile lookup for a username",
               ("username",)),
    ModuleSpec("hackernews-user", ("username",), _simple(hackernews_user.run),
               "engine.hackernews", "Hacker News (Algolia) account lookup for a username",
               ("username",)),
    ModuleSpec("dockerhub-profile", ("username",), _simple(dockerhub_profile.run),
               "engine.dockerhub", "Docker Hub profile lookup for a username",
               ("username",)),
    ModuleSpec("chess-profile", ("username",), _simple(chess_profile.run),
               "engine.chess", "Chess.com public player lookup for a username",
               ("username",)),
    ModuleSpec("lichess-profile", ("username",), _simple(lichess_profile.run),
               "engine.lichess", "Lichess player lookup for a username", ("username",)),
    ModuleSpec("codeberg-profile", ("username",), _simple(codeberg_profile.run),
               "engine.codeberg", "Codeberg (Forgejo/Gitea) account lookup for a username",
               ("username",)),
    ModuleSpec("devto-profile", ("username",), _simple(devto_profile.run),
               "engine.devto", "DEV Community profile lookup for a username",
               ("username",)),
    ModuleSpec("discogs-profile", ("username",), _simple(discogs_profile.run),
               "engine.discogs", "Discogs user profile lookup for a username",
               ("username",)),
    ModuleSpec("wikipedia-user", ("username",), _simple(wikipedia_user.run),
               "engine.wikipedia", "Wikipedia account lookup for a username",
               ("username",)),
    ModuleSpec("archive-creator", ("username",), _simple(archive_creator.run),
               "engine.archive", "Internet Archive uploads credited to a username",
               ("username",)),

    # ------------------------------------------------------------ url leads
    ModuleSpec("fetch", ("url",), _simple(fetch.run),
               "engine.fetch", "Fetch a URL and extract strict identifiers",
               ("url",)),
    ModuleSpec("wayback", ("url",), _simple(wayback.run),
               "engine.wayback", "Wayback Machine CDX history for a URL", ("url",)),
    ModuleSpec("security-headers", ("url", "domain"), _simple(security_headers.run),
               "engine.sec-headers", "HTTP security-header posture probe for a url/domain",
               ("url", "domain"), max_depth=0),

    # --------------------------------------------------------- domain leads
    ModuleSpec("domain-posture", ("domain",), _domain_posture,
               "engine.domain-posture", "DoH MX + certificate transparency + RDAP posture",
               ("domain",)),
]


def modules_for(lead_type: str) -> list[ModuleSpec]:
    """Every module that answers this lead type, in run order."""
    return [m for m in MODULES if lead_type in m.lead_types]


def lead_types_covered() -> list[str]:
    seen: list[str] = []
    for m in MODULES:
        for lt in m.lead_types:
            if lt not in seen:
                seen.append(lt)
    return seen

"""The wildfire engine (v0.3).

Old model: a fixed 8-step pipeline for exactly one seed, depth max 2.
New model: a lead-driven breadth-first walk â€” every CONFIRMED finding of
module M becomes a new typed LEAD, leads keep spawning leads, and the run
ends with a complete footprint graph. Evidence-only rules are unchanged:

  - a node exists only because a specific request returned that specific
    identifier (URL, email, phone, handle, name, domain);
  - every edge carries source + evidence URL + exact snippet;
  - a blocked/ambiguous response is inconclusive, never "not found";
  - usernames are never INVENTED: they are either leads the operator
    typed (user-asserted), handles a real API returned (Breach payload,
    GitHub object), or â€” with the explicit opt-in --wild flag â€”
    deterministic punctuation permutations of the operator's own email
    local-part expressed back to the operator-as-subject.

Lead types and the modules that consume them:
  email    -> gravatar, hibp, github-commit-search, email-checks,
              userrealm, breach-existence, pgp, registration-leaves,
              dns (domain posture), exact-document (web spike)
  phone    -> exact-document
  username -> username-sweep (WMN dataset), github-profile, keybase-proofs,
              reddit-user
  url      -> fetch (+interconnect when the URL is exactly a github.com
              profile root), wayback
  domain   -> domains (DoH MX + crt.sh + RDAP; infra side-list only)
  name     -> node-only (never expanded â€” no guessing modules exist)

Depth: the seed is depth 0, a module output spawned from a depth-d lead is
depth d+1; leads are processed only while depth <= max_depth (default 3).
Caps per type provide the runtime/politeness ceiling on how far one lead
can ignite the graph (seeds always run; caps count only spawned leads).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

import httpx

from thousand_eyes.http_client import new_client
from thousand_eyes.impact import AMBIENT as _AMBIENT_TIER, leaf_tier, pivot_tier
from thousand_eyes.models import NULL_SINK, Leaf, ModuleError, Node, ScanResult, Skipped, normalise_value
from thousand_eyes.modules import (
    breach_existence,
    bluesky_profile,
    chess_profile,
    darkweb,
    dockerhub_profile,
    domains,
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
    mastodon_lookup,
    pgp,
    registration_leaves,
    security_headers,
    username_checks,
    userrealm,
    wayback,
    reddit_user,
)
from thousand_eyes.normalise import normalise_seed
import re as _re

# internet slug of the operator's own local-part, --wild only
_WILD_SPLIT = _re.compile(r"[._\-]+")

# -------- spawn-cap policy (v0.4): default = polite evidence walk;
# --wild = the wildfire spread (bigger reach, derived handles, full sweep).
_DEFAULT_SPAWN_CAPS = {
    "email": 4,
    "phone": 2,
    "url": 24,
    "username": 8,
    "domain": 5,
}
_WILD_SPAWN_CAPS = {
    "email": 8,
    "phone": 4,
    "url": 64,
    "username": 24,
    "domain": 8,
}

# expected engine cost (progress bar units) per enqueued lead type
_LEAD_UNIT_COST = {
    "email": 10,   # gravatar, hibp, gcs, email-checks, userrealm, breach(+rock), pgp, reg-leaves, darkweb, exact-doc
    "username": 6, # wm-sweep, github, keybase, reddit, darkweb (+1 var)
    "url": 3,      # fetch, wayback (+github/keybase pair on profile roots)
    "phone": 1,    # exact-document
    "domain": 1,   # posture run
}

# modules that tick the progress bar internally (per-site), so the engine
# does not add one more unit-bump for them
_SELF_TALLYING_MODULES = {"username-sweep", "email-checks", "registration-leaves", "darkweb"}


# archived snapshots are terminal evidence â€” never re-queued leads
_ARCHIVED_URL_PREFIX = "https://web.archive.org/web/"


@dataclass
class Lead:
    type: str
    value: str
    depth: int
    via: str
    source: str = ""


def _is_github_profile_root(url: str) -> str | None:
    m = _re.match(r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?$", url.strip())
    return m.group(1) if m else None


def _wild_candidates(local: str) -> list[str]:
    """--wild: small deterministic set of punctuation variants of the
    operator's own local-part. The operator supplies the domain-anchored
    identity; these are form variants, not invented names."""
    base = _WILD_SPLIT.sub("", local)
    if not base or len(base) < 3:
        return [local]
    cand = [local, base, f"{base}1", f"{base}_", f"{base}12", f"{base}34"]
    seen: set[str] = set()
    out: list[str] = []
    for c in cand:
        k = c.strip().lower()
        if c and k not in seen:
            seen.add(k)
            out.append(c)
    return out[:6]


def _darkweb_with_note(engine: "_Engine", term: str) -> dict:
    """darkweb.run wrapper that converts the module's inconclusive 'note'
    (anti-scrape landing page) into a skipped-row instead of an error -
    the honest middle between 'no mentions' and 'blocked'."""
    return _run_darkweb(engine, term)


async def _run_darkweb(engine: "_Engine", term: str) -> dict:
    res = await darkweb.run(engine.client, term, sink=engine.sink)
    if res.get("note"):
        engine.result.append_skipped("darkweb", str(res["note"]))
    return res


def _record(result: ScanResult, module_label: str, res: dict) -> None:
    """One place that understands the legacy {error|errors|skipped|...}
    module result shapes and flattens them into the ScanResult."""
    if res.get("error"):
        result.errors.append(ModuleError(module_label, res["error"]))
    for err in res.get("errors") or []:
        if isinstance(err, dict):
            result.errors.append(ModuleError(err.get("module", module_label), err.get("error", "unknown")))
        else:
            result.errors.append(ModuleError(module_label, str(err)))
    if res.get("skipped"):
        reason = res["skipped"]
        if isinstance(reason, dict):
            result.append_skipped(reason.get("module", module_label), reason.get("reason", ""))
        else:
            result.append_skipped(module_label, str(reason))


def _add_pivot(result: ScanResult, from_value: str, pivot: dict) -> Node:
    node = Node(type=pivot["type"], value=pivot["value"], pivot=True)
    added = result.add_node(node)
    result.add_edge(
        from_value=from_value,
        to_value=pivot["value"],
        source=pivot["source"],
        evidence_url=pivot["evidence_url"],
        snippet=pivot["snippet"],
        status=pivot.get("status"),
    )
    return added


class _Engine:
    def __init__(
        self,
        result: ScanResult,
        client: httpx.AsyncClient,
        *,
        max_depth: int,
        wild: bool,
        all_sites: bool,
        sink=None,
    ) -> None:
        self.result = result
        self.client = client
        self.max_depth = max_depth
        self.wild = wild
        self.all_sites = all_sites
        self.sink = sink if sink is not None else NULL_SINK
        self.caps = dict(_WILD_SPAWN_CAPS if wild else _DEFAULT_SPAWN_CAPS)
        self.queue: deque[Lead] = deque()
        self.processed: set[tuple[str, str]] = set()
        self.spawned = {t: 0 for t in self.caps}

    # ---------------- sink plumbing ----------------
    def _stage(self, label: str) -> None:
        self.sink.stage(label)

    def _finding(self, tier: int, kind: str, label: str) -> None:
        self.sink.finding(tier, kind, label)

    def enqueue(self, lead_type: str, value: str, from_value: str, source: str, is_seed: bool = False, depth: int = 1) -> None:
        if not value:
            return
        if lead_type == "domain" and domains.is_free_mailbox(value):
            return  # never spawn posture runs for consumer mailboxes
        key = (lead_type, normalise_value(lead_type, value))
        if key in self.processed:
            return
        # seeds get processed unconditionally; spawned leads are capped
        if not is_seed:
            if self.spawned.get(lead_type, 0) >= self.caps.get(lead_type, 0):
                return
            self.spawned[lead_type] += 1
        self.processed.add(key)
        self.queue.append(Lead(type=lead_type, value=value, depth=depth, via=from_value, source=source))
        self.sink.expect(_LEAD_UNIT_COST.get(lead_type, 1))

    def take(self, lead: Lead, pivots: list[dict]) -> None:
        """Turn a module's pivot dicts into graph nodes/edges + new leads."""
        for p in pivots:
            t, v = p["type"], p["value"]
            if t == "fact":
                # observed facts (account age, posture rows) are evidence
                # nodes but never expandable and never re-classified
                node = Node(type="fact", value=v, pivot=False)
                self.result.add_node(node)
                self.result.add_edge(
                    from_value=lead.value, to_value=v, source=p["source"],
                    evidence_url=p["evidence_url"], snippet=p["snippet"], status=p.get("status"),
                )
                self._finding(_AMBIENT_TIER, "fact", v)
                continue
            if t == "name":
                # names are evidence nodes but never expandable
                node = Node(type="name", value=v, pivot=False)
                self.result.add_node(node)
                self.result.add_edge(
                    from_value=lead.value, to_value=v, source=p["source"],
                    evidence_url=p["evidence_url"], snippet=p["snippet"], status=p.get("status"),
                )
                tier, _ = pivot_tier("name", v)
                self._finding(tier, "name", v)
                continue
            if t == lead.type and normalise_value(t, v) == normalise_value(t, lead.value):
                continue  # self-loop guard: a lead rediscovering itself adds noise
            node = _add_pivot(self.result, lead.value, p)
            if not node.label:
                node.label = p.get("site", "") or ""
                node.category = p.get("category", "") or ""
            tier, _ = pivot_tier(t, v, node.category, node.label)
            self._finding(tier, node.label or t, v)
            if lead.depth + 1 <= self.max_depth:
                if t in ("url", "email", "phone", "username", "domain"):
                    if t == "url" and v.startswith(_ARCHIVED_URL_PREFIX):
                        continue  # wayback recursion guard: snapshots are terminal evidence
                    self.enqueue(t, v, lead.value, p["source"], depth=lead.depth + 1)

    async def _run_email_modules(self, lead: Lead) -> None:
        email = lead.value
        email_domain = email.rsplit("@", 1)[-1]

        # Pre-claim the domain dedup key BEFORE the module gather: the email
        # arm runs domain posture inline below, so userrealm's domain pivot
        # must not enqueue a second identical domain processing later.
        if not domains.is_free_mailbox(email_domain.lower()):
            self.processed.add(("domain", normalise_value("domain", email_domain)))

        # (label, coro-factory, self-tallying?) â€” sweeps tick per site inside
        simple = [
            ("gravatar", lambda: gravatar.run(self.client, email)),
            ("hibp", lambda: hibp.run(self.client, email)),
            ("github-commit-search", lambda: github_commit_search.run(self.client, email)),
            ("userrealm", lambda: userrealm.run(self.client, email)),
            ("pgp", lambda: pgp.run(self.client, email)),
        ]
        sweeps = [
            ("email-checks", lambda: email_checks.run(self.client, email, sink=self.sink)),
            ("breach-existence", lambda: self._breach_pair(email)),
            ("registration-leaves", lambda: registration_leaves.run(self.client, email, sink=self.sink)),
            ("darkweb", lambda: _darkweb_with_note(self, email)),
            ("exact-document", lambda: exact_document.run(self.client, email)),
        ]
        pairs: list[tuple[str, dict]] = []
        for label, factory in simple:
            self._stage(f"email[{email}] â†’ {label}")
            try:
                res = await factory()
            except httpx.HTTPError as exc:
                res = {"pivots": [], "leaves": [], "error": f"engine: {type(exc).__name__}: {exc}"}
            self.sink.bump()
            pairs.append((label, res))
        for label, factory in sweeps:
            self._stage(f"email[{email}] â†’ {label}")
            try:
                res = await factory()
            except httpx.HTTPError as exc:
                res = {"pivots": [], "leaves": [], "error": f"engine: {type(exc).__name__}: {exc}"}
            pairs.append((label, res))
        for label, res in pairs:
            _record(self.result, label, res)
            self.take(lead, res.get("pivots") or [])
            for leaf in res.get("leaves") or []:
                cat = leaf.get("category", "")
                added = self.result.append_leaf(leaf["site"], leaf["url"], leaf.get("status"), cat)
                if added:
                    tier, _ = leaf_tier(leaf["site"], cat)
                    self._finding(tier, cat or "leaf", leaf["site"])

        email_domain = email.rsplit("@", 1)[-1]
        if domains.is_free_mailbox(email_domain.lower()):
            self.result.append_skipped("dns", f"{email_domain} is a free mailbox provider, domain arm skipped")
        else:
            self._stage(f"email[{email}] â†’ domain posture")
            dns_res = await domains.run_domain(self.client, email_domain)
            for h in dns_res.get("errors") or []:
                self.result.errors.append(ModuleError("dns", str(h)))
            for host in dns_res.get("hosts") or []:
                if self.result.append_leaf(f"mail infrastructure: {host['value']}", host["evidence_url"], None, "infrastructure"):
                    self._finding(_AMBIENT_TIER, "infra", host["value"])

        if self.wild:
            local = email.rsplit("@", 1)[0]
            self._stage("derived handles from local-part (--wild)")
            for cand in _wild_candidates(local):
                self.enqueue("username", cand, email, "wild:localpart-permutation", depth=lead.depth + 1)

    async def _breach_pair(self, email: str) -> dict:
        """xposedornot keyless check plus the optional Hudson Rock stealer-log
        check (skips cleanly without a key). Union of leaves/errors."""
        res = await breach_existence.run(self.client, email)
        rock = await breach_existence.run_hudsonrock(self.client, email)
        if rock.get("skipped"):
            res.setdefault("skipped", rock.get("skipped"))
        for e in rock.get("errors") or []:
            res.setdefault("errors", []).append(e)
        res.setdefault("leaves", []).extend(rock.get("leaves") or [])
        return res

    async def _run_phone_modules(self, lead: Lead) -> None:
        self._stage(f"phone[{lead.value}] â†’ exact-document")
        res = await exact_document.run(self.client, lead.value)
        self.sink.bump()
        _record(self.result, "exact-document", res)
        self.take(lead, res.get("pivots") or [])

    async def _run_username_modules(self, lead: Lead) -> None:
        username = lead.value
        self._stage(f"username[{username}] â†’ github/keybase")
        res_gh = await github_profile.run(self.client, username)
        self.sink.bump()
        res_kb = await keybase_proofs.run(self.client, username)
        self.sink.bump()
        res_gl = await gitlab_profile.run(self.client, username)
        self.sink.bump()
        self._stage(f"username[{username}] â†’ reddit")
        res_reddit = await reddit_user.run(self.client, username)
        self.sink.bump()
        res_mast = await mastodon_lookup.run(self.client, username)
        self.sink.bump()
        res_bsky = await bluesky_profile.run(self.client, username)
        self.sink.bump()
        res_hn = await hackernews_user.run(self.client, username)
        self.sink.bump()
        res_dh = await dockerhub_profile.run(self.client, username)
        self.sink.bump()
        res_chess = await chess_profile.run(self.client, username)
        self.sink.bump()
        for label, res in (
            ("github-profile", res_gh),
            ("keybase-proofs", res_kb),
            ("gitlab-profile", res_gl),
            ("reddit-user", res_reddit),
            ("mastodon-lookup", res_mast),
            ("bluesky-profile", res_bsky),
            ("hackernews-user", res_hn),
            ("dockerhub-profile", res_dh),
            ("chess-profile", res_chess),
        ):
            _record(self.result, label, res)
            self.take(lead, res.get("pivots") or [])
        self._stage(f"username[{username}] â†’ platform sweep (WMN)")
        # derived handles (wild permutations) get the polite 120-site sweep -
        # only the operator-typed handle earns the full 692-site stroll
        derived = lead.source.startswith('wild:')
        res_sweep = await username_checks.run(self.client, username, all_sites=self.all_sites and not derived, sink=self.sink)
        _record(self.result, "username-sweep", res_sweep)
        self.take(lead, res_sweep.get("pivots") or [])
        self._stage(f"username[{username}] â†’ darknet index")
        res_dark = await _run_darkweb(self, username)
        for err in res_dark.get("errors") or []:
            self.result.errors.append(ModuleError("darkweb", err))
        for leaf in res_dark.get("leaves") or []:
            if self.result.append_leaf(leaf["site"], leaf["url"], leaf.get("status"), "darknet"):
                tier, _ = leaf_tier(leaf["site"], "darknet")
                self._finding(tier, "darknet", leaf["site"])

    async def _run_url_modules(self, lead: Lead) -> None:
        url = lead.value
        self._stage(f"url[{url[:60]}] â†’ fetch+extract")
        res_fetch = await fetch.run(self.client, url)
        self.sink.bump()
        _record(self.result, "fetch", res_fetch)
        self.take(lead, res_fetch.get("pivots") or [])

        github_username = _is_github_profile_root(url)
        if github_username:
            res_gh = await github_profile.run(self.client, github_username)
            self.sink.bump()
            _record(self.result, "github-profile", res_gh)
            self.take(lead, res_gh.get("pivots") or [])
            res_kb = await keybase_proofs.run(self.client, github_username)
            self.sink.bump()
            _record(self.result, "keybase-proofs", res_kb)
            self.take(lead, res_kb.get("pivots") or [])

        self._stage(f"url[{url[:60]}] â†’ wayback")
        res_wmb = await wayback.run(self.client, url)
        self.sink.bump()
        _record(self.result, "wayback", res_wmb)
        self.take(lead, res_wmb.get("pivots") or [])

        # posture probe: only for the operator's OWN lead (depth 0). Running
        # it on every spawned pivot buries the dossier in header noise.
        if lead.depth == 0:
            self._stage(f"url[{url[:60]}] → security headers")
            res_sh = await security_headers.run(self.client, url)
            self.sink.bump()
            _record(self.result, "security-headers", res_sh)
            self.take(lead, res_sh.get("pivots") or [])

    async def _run_domain_modules(self, lead: Lead) -> None:
        self._stage(f"domain[{lead.value}] â†’ posture")
        res = await domains.run_domain(self.client, lead.value)
        self.sink.bump()
        for err in res.get("errors") or []:
            self.result.errors.append(ModuleError("domains", str(err)))
        for host in res.get("hosts") or []:
            if self.result.append_leaf(f"mail infrastructure: {host['value']}", host["evidence_url"], None, "infrastructure"):
                self._finding(_AMBIENT_TIER, "infra", host["value"])

        # posture probe: seed domains only (see the url path for the why)
        if lead.depth == 0:
            self._stage(f"domain[{lead.value}] → security headers")
            res_sh = await security_headers.run(self.client, lead.value)
            self.sink.bump()
            _record(self.result, "security-headers", res_sh)
            self.take(lead, res_sh.get("pivots") or [])

    async def run(self, seeds: list[tuple[str, str]]) -> None:
        for lead_type, value in seeds:
            seed_lead = Lead(type=lead_type, value=value, depth=0, via=value)
            node = Node(type=lead_type, value=value, pivot=True)
            self.result.add_node(node)
            self.enqueue(lead_type, value, value, "seed", is_seed=True, depth=0)

        while self.queue:
            lead = self.queue.popleft()
            if lead.depth > self.max_depth:
                continue
            handler = {
                "email": self._run_email_modules,
                "phone": self._run_phone_modules,
                "username": self._run_username_modules,
                "url": self._run_url_modules,
                "domain": self._run_domain_modules,
            }.get(lead.type)
            if handler is None:
                continue
            try:
                await handler(lead)
            except httpx.HTTPError as exc:
                # per-module HTTPError layers are caught inside each module itself;
                # a raw exception slipping out here is recorded, never fatal
                self.result.errors.append(ModuleError(lead.type, f"engine: {type(exc).__name__}: {exc}"))


async def run_scan(
    raw_seeds: str | list[str],
    *,
    fold_plus: bool = False,
    max_depth: int = 3,
    wild: bool = False,
    all_sites: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    url_log: str | None = None,
    sink=None,
) -> ScanResult:
    """Wildfire scan. `transport` is test-only: pass an httpx.MockTransport to
    run the full pipeline against a fixture server instead of the live network.
    `sink` receives live progress/finding events (NullSink when omitted).
    Accepts one seed (str) or multiple seeds (list) â€” every one becomes a
    first-class root lead of the graph."""
    import time as _time
    started = _time.perf_counter()
    if isinstance(raw_seeds, str):
        raw_seeds = [raw_seeds]

    seeds: list[tuple[str, str]] = []
    seen_seed: set[tuple[str, str]] = set()
    for raw in raw_seeds:
        seed = normalise_seed(raw, fold_plus=fold_plus)
        key = (seed.type, normalise_value(seed.type, seed.value))
        if key in seen_seed:
            continue
        seen_seed.add(key)
        seeds.append((seed.type, seed.value))

    result = ScanResult(seed=", ".join(s[1] for s in seeds) or "", seed_type=",".join(s[0] for s in seeds) or "none")

    async with new_client(transport=transport, url_log_path=url_log) as client:
        engine = _Engine(
            result,
            client,
            max_depth=max_depth,
            wild=wild,
            all_sites=all_sites,
            sink=sink,
        )
        for lead_type, value in seeds:
            engine.sink.expect(_LEAD_UNIT_COST.get(lead_type, 1))
        await engine.run(seeds)

    result.meta["duration_seconds"] = round(_time.perf_counter() - started, 2)
    result.meta["wild"] = bool(wild)
    result.meta["all_sites"] = bool(all_sites)
    return result


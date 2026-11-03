"""The wildfire engine (v0.3).

Old model: a fixed 8-step pipeline for exactly one seed, depth max 2.
New model: a lead-driven breadth-first walk — every CONFIRMED finding of
module M becomes a new typed LEAD, leads keep spawning leads, and the run
ends with a complete footprint graph. Evidence-only rules are unchanged:

  - a node exists only because a specific request returned that specific
    identifier (URL, email, phone, handle, name, domain);
  - every edge carries source + evidence URL + exact snippet;
  - a blocked/ambiguous response is inconclusive, never "not found";
  - usernames are never INVENTED: they are either leads the operator
    typed (user-asserted), handles a real API returned (Breach payload,
    GitHub object), or — with the explicit opt-in --wild flag —
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
  name     -> node-only (never expanded — no guessing modules exist)

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
from thousand_eyes.models import Leaf, ModuleError, Node, ScanResult, Skipped, normalise_value
from thousand_eyes.modules import (
    breach_existence,
    domains,
    email_checks,
    exact_document,
    fetch,
    github_commit_search,
    github_profile,
    gravatar,
    hibp,
    keybase_proofs,
    pgp,
    registration_leaves,
    username_checks,
    userrealm,
    wayback,
    reddit_user,
)
from thousand_eyes.normalise import normalise_seed
import re as _re

# internet slug of the operator's own local-part, --wild only
_WILD_SPLIT = _re.compile(r"[._\-]+")

# per-type spawned-lead caps (seeds always run regardless)
_SPAWN_CAPS = {
    "email": 4,
    "phone": 2,
    "url": 24,
    "username": 8,
    "domain": 5,
}


# archived snapshots are terminal evidence — never re-queued leads
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
            result.skipped.append(Skipped(reason.get("module", module_label), reason.get("reason", "")))
        else:
            result.skipped.append(Skipped(module_label, str(reason)))


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
    ) -> None:
        self.result = result
        self.client = client
        self.max_depth = max_depth
        self.wild = wild
        self.all_sites = all_sites
        self.queue: deque[Lead] = deque()
        self.processed: set[tuple[str, str]] = set()
        self.spawned = {t: 0 for t in _SPAWN_CAPS}

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
            if self.spawned.get(lead_type, 0) >= _SPAWN_CAPS.get(lead_type, 0):
                return
            self.spawned[lead_type] += 1
        self.processed.add(key)
        self.queue.append(Lead(type=lead_type, value=value, depth=depth, via=from_value, source=source))

    def take(self, lead: Lead, pivots: list[dict]) -> None:
        """Turn a module's pivot dicts into graph nodes/edges + new leads."""
        for p in pivots:
            if p.get("type") == "name":
                # names are evidence nodes but never expandable
                node = Node(type="name", value=p["value"], pivot=False)
                self.result.add_node(node)
                self.result.add_edge(
                    from_value=p["source"], to_value=p["value"], source=p["source"],
                    evidence_url=p["evidence_url"], snippet=p["snippet"], status=p.get("status"),
                )
                continue
            _add_pivot(self.result, lead.via, p)
            t, v = p["type"], p["value"]
            if lead.depth + 1 <= self.max_depth:
                if t in ("url", "email", "phone", "username", "domain"):
                    self.enqueue(t, v, lead.via, p["source"])

    def take(self, lead: Lead, pivots: list[dict]) -> None:
        """Turn a module's pivot dicts into graph nodes/edges + new leads."""
        for p in pivots:
            t, v = p["type"], p["value"]
            if t == "name":
                # names are evidence nodes but never expandable
                node = Node(type="name", value=v, pivot=False)
                self.result.add_node(node)
                self.result.add_edge(
                    from_value=lead.value, to_value=v, source=p["source"],
                    evidence_url=p["evidence_url"], snippet=p["snippet"], status=p.get("status"),
                )
                continue
            if t == lead.type and normalise_value(t, v) == normalise_value(t, lead.value):
                continue  # self-loop guard: a lead rediscovering itself adds noise
            _add_pivot(self.result, lead.value, p)
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

        pairs = await asyncio.gather(
            gravatar.run(self.client, email),
            hibp.run(self.client, email),
            github_commit_search.run(self.client, email),
            email_checks.run(self.client, email),
            userrealm.run(self.client, email),
            breach_existence.run(self.client, email),
            pgp.run(self.client, email),
            registration_leaves.run(self.client, email),
        )
        labels = ["gravatar", "hibp", "github-commit-search", "email-checks",
                  "userrealm", "breach-existence", "pgp", "registration-leaves"]
        for label, res in zip(labels, pairs):
            _record(self.result, label, res)
            self.take(lead, res.get("pivots") or [])
            for leaf in res.get("leaves") or []:
                self.result.leaves.append(Leaf(site=leaf["site"], url=leaf["url"], status=leaf.get("status")))

        email_domain = email.rsplit("@", 1)[-1]
        if domains.is_free_mailbox(email_domain.lower()):
            self.result.skipped.append(Skipped("dns", f"{email_domain} is a free mailbox provider, domain arm skipped"))
        else:
            dns_res = await domains.run_domain(self.client, email_domain)
            for h in dns_res.get("errors") or []:
                self.result.errors.append(ModuleError("dns", str(h)))
            for host in dns_res.get("hosts") or []:
                self.result.leaves.append(Leaf(site=f"mail infrastructure: {host['value']}", url=host["evidence_url"], status=None))

        if self.wild:
            local = email.rsplit("@", 1)[0]
            for cand in _wild_candidates(local):
                self.enqueue("username", cand, email, "wild:localpart-permutation", depth=lead.depth + 1)

    async def _run_phone_modules(self, lead: Lead) -> None:
        res = await exact_document.run(self.client, lead.value)
        _record(self.result, "exact-document", res)
        self.take(lead, res.get("pivots") or [])

    async def _run_username_modules(self, lead: Lead) -> None:
        username = lead.value
        res_sweep, res_gh, res_kb = await asyncio.gather(
            username_checks.run(self.client, username, all_sites=self.all_sites),
            github_profile.run(self.client, username),
            keybase_proofs.run(self.client, username),
        )
        res_reddit = await reddit_user.run(self.client, username)
        for label, res in (("username-sweep", res_sweep), ("github-profile", res_gh),
                           ("keybase-proofs", res_kb), ("reddit-user", res_reddit)):
            _record(self.result, label, res)
            self.take(lead, res.get("pivots") or [])

    async def _run_url_modules(self, lead: Lead) -> None:
        url = lead.value
        res_fetch = await fetch.run(self.client, url)
        _record(self.result, "fetch", res_fetch)
        self.take(lead, res_fetch.get("pivots") or [])

        github_username = _is_github_profile_root(url)
        if github_username:
            res_gh = await github_profile.run(self.client, github_username)
            _record(self.result, "github-profile", res_gh)
            self.take(lead, res_gh.get("pivots") or [])
            res_kb = await keybase_proofs.run(self.client, github_username)
            _record(self.result, "keybase-proofs", res_kb)
            self.take(lead, res_kb.get("pivots") or [])

        res_wmb = await wayback.run(self.client, url)
        _record(self.result, "wayback", res_wmb)
        self.take(lead, res_wmb.get("pivots") or [])

    async def _run_domain_modules(self, lead: Lead) -> None:
        res = await domains.run_domain(self.client, lead.value)
        for err in res.get("errors") or []:
            self.result.errors.append(ModuleError("domains", str(err)))
        for host in res.get("hosts") or []:
            self.result.leaves.append(Leaf(site=f"mail infrastructure: {host['value']}", url=host["evidence_url"], status=None))

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
) -> ScanResult:
    """Wildfire scan. `transport` is test-only: pass an httpx.MockTransport to
    run the full pipeline against a fixture server instead of the live network.
    Accepts one seed (str) or multiple seeds (list) — every one becomes a
    first-class root lead of the graph."""
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
        )
        await engine.run(seeds)

    return result

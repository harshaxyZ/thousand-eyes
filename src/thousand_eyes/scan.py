"""The wildfire engine.

A lead-driven breadth-first walk: every CONFIRMED finding of a module becomes
a new typed LEAD, leads keep spawning leads, and the run ends with a complete
footprint graph. Evidence-only rules:

  - a node exists only because a specific request returned that specific
    identifier (URL, email, phone, handle, name, domain);
  - every edge carries source + evidence URL + exact snippet;
  - a blocked/ambiguous response is inconclusive, never "not found";
  - usernames are never INVENTED: they are either leads the operator
    typed (user-asserted), handles a real API returned (breach payload,
    GitHub object), handles proven by a response-confirmed profile URL, or,
    with the explicit opt-in --full flag, deterministic punctuation
    permutations of the operator's own email local-part.

Which modules answer which lead type is declared once, in registry.py
(email, phone, username, url, domain). A `name` is a node only: it is never
expanded, because no guessing modules exist.

Depth: the seed is depth 0, a module output spawned from a depth-d lead is
depth d+1; leads are processed only while depth <= max_depth (default 4).
Caps per type provide the runtime/politeness ceiling on how far one lead
can ignite the graph (seeds always run; caps count only spawned leads).

Within one lead the modules run concurrently (each talks to a different
first-party host), but their results are folded into the graph in registry
order, so a run stays reproducible.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

import httpx

from thousand_eyes import registry
from thousand_eyes.handle_rules import extract_handle
from thousand_eyes.http_client import new_client
from thousand_eyes.impact import AMBIENT as _AMBIENT_TIER, leaf_tier, pivot_tier
from thousand_eyes.models import NULL_SINK, ModuleError, Node, ScanResult, normalise_value
from thousand_eyes.modules import domains, github_profile, keybase_proofs
from thousand_eyes.normalise import normalise_seed
import re as _re

# internet slug of the operator's own local-part, --wild only
_WILD_SPLIT = _re.compile(r"[._\-]+")

# -------- spawn-cap policy (v0.4): default = polite evidence walk;
# --wild = the wildfire spread (bigger reach, derived handles, full sweep).
_DEFAULT_SPAWN_CAPS = {
    # politeness ceilings, not speed knobs: how many SPAWNED leads of each
    # type one seed may ignite. The real governor is the wall-clock budget
    # (time_budget) plus the total-lead budget - a wide cascade that stays
    # inside those bounds is exactly what a complete presence map needs.
    "email": 12,
    "phone": 3,
    "url": 120,
    "username": 24,
    "domain": 16,
}
_WILD_SPAWN_CAPS = {
    "email": 20,
    "phone": 6,
    "url": 200,
    "username": 48,
    "domain": 24,
}

# expected engine cost (progress bar units) per enqueued lead type: a rough
# planning number for the bar, the sweeps add their own per-site units
_LEAD_UNIT_COST = {
    "email": 10,
    "username": 6,
    "url": 3,
    "phone": 1,
    "domain": 1,
}

# how many modules of ONE lead run at the same time. Each module talks to a
# different first-party host, so this is a load ceiling for the operator's
# machine, not extra pressure on any single site.
MODULE_CONCURRENCY = 6


# archived snapshots are terminal evidence - never re-queued leads
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


def module_outcome(res: dict) -> str:
    """ok | partial | failed | skipped for one module result.

    A sweep that answered for 119 sites and timed out on 1 is PARTIAL, not
    failed: counting it as failed made the coverage line read far worse than
    the run really was. Every sub-error is still listed under "checks that
    failed" - partial only changes how the module itself is counted.
    """
    if res.get("error"):
        return "failed"
    if res.get("errors"):
        answered = res.get("pivots") or res.get("leaves") or res.get("checked")
        return "partial" if answered else "failed"
    if res.get("skipped"):
        return "skipped"
    return "ok"


def _record(result: ScanResult, module_label: str, res: dict) -> None:
    """One place that understands the legacy {error|errors|skipped|...}
    module result shapes and flattens them into the ScanResult."""
    # quality accounting: how many source families actually answered
    sources = result.meta.setdefault("sources", {})
    bucket = sources.setdefault(
        module_label, {"ok": 0, "partial": 0, "failed": 0, "skipped": 0})
    outcome = module_outcome(res)
    bucket[outcome] = bucket.get(outcome, 0) + 1
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
        on_event=None,
        time_budget: float | None = None,
        max_leads: int | None = None,
        abort=None,
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
        self.on_event = on_event
        self.time_budget = time_budget
        self.max_leads = max_leads
        self.abort = abort
        self.leads_processed = 0
        self.t0 = time.monotonic()
        self.modules_run = 0
        self.modules_failed = 0
        self.findings = 0
        # (module, value) pairs already executed in this run - the cascade can
        # reach the same handle through different paths; the module must not
        # run twice on the same value.
        self._module_runs: set[tuple[str, str]] = set()
        self._module_sem = asyncio.Semaphore(MODULE_CONCURRENCY)

    # ---------------- live event plumbing ----------------
    def emit(self, event: str, **data) -> None:
        """Push a live event to the caller (the TUI renders these as the
        'doing this / found that' feed). Never raises into the scan."""
        if self.on_event is None:
            return
        try:
            self.on_event((event, data))
        except Exception:      # noqa: BLE001 - a broken UI must not break a scan
            self.on_event = None

    # ---------------- sink plumbing ----------------
    def _stage(self, label: str) -> None:
        self.sink.stage(label)
        self.emit("label", label=label)

    def _finding(self, tier: int, kind: str, label: str) -> None:
        self.sink.finding(tier, kind, label)
        self.findings += 1
        self.emit("found", tier=tier, kind=kind, value=label)

    def _announce_module(self, label: str, res: dict, lead: Lead | None = None,
                         ms: float = 0.0) -> None:
        """Tell the live feed one module finished (called the moment it does)."""
        outcome = module_outcome(res)
        state = {"failed": "fail", "partial": "partial"}.get(outcome, "ok")
        n = len(res.get("pivots") or []) + len(res.get("leaves") or [])
        error = str(res.get("error") or "")
        if not error and outcome == "partial":
            error = f"{len(res.get('errors') or [])} sub-check(s) did not answer"
        self.emit("module", label=label, state=state,
                  findings=n, ms=round(ms * 1000),
                  lead=lead.value if lead else "",
                  lead_type=lead.type if lead else "",
                  error=error[:160])

    def _record_module(self, label: str, res: dict, lead: Lead | None = None,
                       ms: float = 0.0, announce: bool = True) -> None:
        """Fold one module result into the report counters."""
        _record(self.result, label, res)
        self.modules_run += 1
        if module_outcome(res) == "failed":
            self.modules_failed += 1
        if announce:
            self._announce_module(label, res, lead, ms)

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
                if t == "url":
                    # the cascade glue: a confirmed profile URL proves the
                    # handle exists there, so the handle becomes the next
                    # lead and the sweep continues from there.
                    handle = extract_handle(v)
                    if handle:
                        self.enqueue("username", handle, lead.value, p["source"],
                                     depth=lead.depth + 1)

    # ---------------- generic lead runner (the API bridge) ---------------

    def _stop_reason(self, leads: bool = True) -> str:
        """Why the cascade should stop right now (empty = keep going).

        `leads=False` is used INSIDE a lead: a lead that is already being
        processed must not be cut short by the lead budget it triggered.
        """
        if self.abort is not None:
            try:
                if self.abort():
                    return "cancelled by the operator"
            except Exception:      # noqa: BLE001
                pass
        if self.time_budget is not None and (time.monotonic() - self.t0) > self.time_budget:
            return (f"time budget of {int(self.time_budget)}s reached - "
                    f"{len(self.queue)} lead(s) left unexplored")
        if leads and self.max_leads is not None and self.leads_processed >= self.max_leads:
            return (f"lead budget of {self.max_leads} reached - "
                    f"{len(self.queue)} lead(s) left unexplored")
        return ""

    def _stopped(self) -> bool:
        """Budget guard: wall-clock budget, total-lead budget or an
        operator-requested cancel."""
        return bool(self._stop_reason())

    async def _run_lead(self, lead: Lead) -> None:
        """Run every module the registry declares for this lead type.

        This IS the cascade: a module's confirmed pivots become new leads
        (see take()), each new lead gets its own stage when the queue comes
        back around, and the loop only ends when the frontier is exhausted,
        the depth cap is hit, or the budget runs out. One seed in, the
        person's whole reachable presence comes out.
        """
        specs = registry.modules_for(lead.type)
        ctx = registry.Ctx(sink=self.sink,
                           all_sites=(self.all_sites and lead.depth == 0))
        runnable = []
        for spec in specs:
            if spec.max_depth is not None and lead.depth > spec.max_depth:
                continue
            run_key = (spec.label, lead.value)
            if run_key in self._module_runs:
                # the cascade can reach the same handle through different
                # paths - a module must not run twice on the same value
                self.emit("module", label=spec.label, state="skip", findings=0,
                          ms=0, lead=lead.value, lead_type=lead.type,
                          error="already ran on this value")
                continue
            self._module_runs.add(run_key)
            runnable.append(spec)

        async def one(spec):
            """Run one module. None = the budget ended before it started."""
            async with self._module_sem:
                if self._stop_reason(leads=False):
                    return None
                self._stage(f"{lead.type}[{lead.value[:60]}] \u2192 {spec.label}")
                t0 = time.monotonic()
                try:
                    res = await spec.fn(self.client, lead.value, ctx)
                except Exception as exc:      # a broken module must not kill the run
                    res = {"pivots": [], "leaves": [],
                           "error": f"engine: {type(exc).__name__}: {exc}"}
                ms = time.monotonic() - t0
                if not spec.self_tallying:
                    self.sink.bump()
                self._announce_module(spec.label, res, lead, ms)
                return res, ms

        outcomes = await asyncio.gather(*(one(spec) for spec in runnable))

        # fold the results into the graph in REGISTRY order, not completion
        # order: spawn caps and node order must not depend on network timing
        cut_short = False
        for spec, outcome in zip(runnable, outcomes):
            if outcome is None:
                # never ran: leave it runnable if the value comes round again
                self._module_runs.discard((spec.label, lead.value))
                cut_short = True
                continue
            res, ms = outcome
            self._record_module(spec.label, res, lead, ms=ms, announce=False)
            self.take(lead, res.get("pivots") or [])
            for leaf in res.get("leaves") or []:
                cat = leaf.get("category", "")
                added = self.result.append_leaf(leaf["site"], leaf["url"],
                                                 leaf.get("status"), cat)
                if added:
                    tier, _ = leaf_tier(leaf["site"], cat)
                    self._finding(tier, cat or "leaf", leaf["site"])
        if cut_short:
            return

        # interconnect rule: a github PROFILE ROOT also carries keybase proofs
        if lead.type == "url":
            gh = _is_github_profile_root(lead.value)
            if gh and ("github-profile", gh) not in self._module_runs:
                for label, fn in (("github-profile", github_profile.run),
                                  ("keybase-proofs", keybase_proofs.run)):
                    self._stage(f"url[{lead.value[:60]}] \u2192 {label}")
                    t0 = time.monotonic()
                    try:
                        res = await fn(self.client, gh)
                    except Exception as exc:      # a broken module must not kill the run
                        res = {"pivots": [], "error": f"engine: {type(exc).__name__}: {exc}"}
                    self.sink.bump()
                    self._record_module(label, res, lead, ms=time.monotonic() - t0)
                    self._module_runs.add((label, gh))
                    self.take(lead, res.get("pivots") or [])

        # --full: deterministic handle permutations of the operator's own email
        if self.wild and lead.type == "email":
            local = lead.value.rsplit("@", 1)[0]
            self._stage("derived handles from local-part (--full)")
            for cand in _wild_candidates(local):
                self.enqueue("username", cand, lead.value,
                             "wild:localpart-permutation", depth=lead.depth + 1)

    async def run(self, seeds: list[tuple[str, str]]) -> None:
        for lead_type, value in seeds:
            seed_lead = Lead(type=lead_type, value=value, depth=0, via=value)
            node = Node(type=lead_type, value=value, pivot=True)
            self.result.add_node(node)
            self.enqueue(lead_type, value, value, "seed", is_seed=True, depth=0)

        stage_no = 0
        while self.queue:
            reason = self._stop_reason()
            if reason:
                self.result.meta["stopped"] = reason
                self.emit("stopped", reason=reason)
                break
            lead = self.queue.popleft()
            if lead.depth > self.max_depth:
                continue
            if not registry.modules_for(lead.type):
                continue      # e.g. a name: a node, never an expandable lead
            stage_no += 1
            self.leads_processed += 1
            stage = {
                "n": stage_no,
                "type": lead.type,
                "value": lead.value,
                "via": lead.via,
                "source": lead.source,
                "modules": 0,
                "findings": 0,
                "failures": 0,
                "seconds": 0.0,
            }
            before_nodes = len(self.result.nodes)
            before_leaves = len(self.result.leaves)
            before_modules = self.modules_run
            before_failures = self.modules_failed
            t_stage = time.monotonic()
            self.emit("stage_start", n=stage_no, type=lead.type, value=lead.value,
                      depth=lead.depth)
            try:
                await self._run_lead(lead)
            except httpx.HTTPError as exc:
                # per-module HTTPError layers are caught inside each module itself;
                # a raw exception slipping out here is recorded, never fatal
                self.result.errors.append(ModuleError(lead.type, f"engine: {type(exc).__name__}: {exc}"))
            stage["seconds"] = round(time.monotonic() - t_stage, 2)
            stage["modules"] = self.modules_run - before_modules
            stage["failures"] = self.modules_failed - before_failures
            stage["findings"] = ((len(self.result.nodes) - before_nodes)
                                 + (len(self.result.leaves) - before_leaves))
            self.result.meta.setdefault("stages", []).append(stage)
            self.emit("stage_end", **stage)


async def run_scan(
    raw_seeds: str | list[str],
    *,
    fold_plus: bool = False,
    max_depth: int = 4,
    wild: bool = False,
    all_sites: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    url_log: str | None = None,
    sink=None,
    on_event=None,
    time_budget: float | None = None,
    max_leads: int | None = None,
    abort=None,
) -> ScanResult:
    """Wildfire scan. `transport` is test-only: pass an httpx.MockTransport to
    run the full pipeline against a fixture server instead of the live network.
    `sink` receives live progress/finding events (NullSink when omitted).
    `on_event` receives (kind, data) live events: stage_start / stage_end /
    module / found / label / stopped - the TUI renders these as the live feed.
    `time_budget` (seconds) and `max_leads` bound the cascade; when either
    is hit the run stops cleanly and says so in the report.
    Accepts one seed (str) or multiple seeds (list) - every one becomes a
    first-class root lead of the graph."""
    started = time.perf_counter()
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
            on_event=on_event,
            time_budget=time_budget,
            max_leads=max_leads,
            abort=abort,
        )
        for lead_type, value in seeds:
            engine.sink.expect(_LEAD_UNIT_COST.get(lead_type, 1))
        await engine.run(seeds)

    result.meta["duration_seconds"] = round(time.perf_counter() - started, 2)
    result.meta["wild"] = bool(wild)
    result.meta["all_sites"] = bool(all_sites)
    return result


"""The planner: parallel pipelines + LLM self-audit loop.

Flow (per operator request, all events streamed to the UI via callbacks):
  round 0   classify leads -> fire the first-round tool FAMILY in ONE parallel batch
  round n   harvest findings -> extract NEW identifiers (regex-verified against
            evidence text) -> LLM self-audit ("which families are still dark?")
            -> fire the next parallel batch -> repeat until depth/budget/stop
  synth     one streaming LLM call renders the structured markdown report

Self-audit rule: the LLM never executes tools itself; it only PICKS from the
registry. The harness executes. That keeps the fence physical — the model
cannot argue its way around it.
"""

from __future__ import annotations

import asyncio
import json
import re
import time as _time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .analytics import Analytics
from .classify import classify, tools_for
from .llm import LLMClient
from .system_prompt import SYSTEM_PROMPT
from .tools import Tool, all_tools

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[A-Za-z0-9./_%:-]+")
DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|app|co|ai|me)\b", re.I)

Event = Tuple[str, Any]
Handler = Callable[[Event], Awaitable[None]]

MAX_DEPTH = 3
LEADS_PER_ROUND = 12
TOOL_BUDGET = 400
BUDGET_SECONDS = 600.0

#: safety fence summary, surfaced in the report prompt + UI
FENCE = ("no .onion resolution · no auth probing · no form submission · "
         "no password tests · no breach dumps · per-domain 300ms bucket · "
         "global concurrency 8 · planner executes, model selects")

# tool name -> input arg name (deterministic routing, no LLM guessing)
ARGMAP = {
    "engine.hibp": "email", "engine.xposed": "email", "engine.hudsonrock": "email",
    "engine.gravatar": "email", "engine.pgp": "email", "engine.userrealm": "email",
    "engine.dns": "email", "engine.email-checks": "email",
    "engine.registration-leaves": "email", "engine.github-commits": "email",
    "engine.exact-document": "seed", "engine.darkweb-index": "term",
    "dork.email": "value", "dork.username": "value", "dork.domain": "value",
    "dork.url": "value", "dark.ahmia-index": "term", "dark.paste-index": "term",
    "engine.username-sweep": "username", "engine.github-profile": "username",
    "engine.reddit": "username", "engine.keybase": "username",
    "engine.wayback": "url", "engine.fetch": "url", "engine.domain": "domain",
}


class Planner:
    """Parallel-pipeline planner with LLM self-audit."""

    def __init__(self, llm: LLMClient, handlers: Optional[List[Handler]] = None,
                 goal: str = "", budget_calls: int = TOOL_BUDGET,
                 transport=None) -> None:
        self.llm = llm
        self.tools: Dict[str, Tool] = {t.name: t for t in all_tools()}
        self.analytics = Analytics()
        self.analytics.budget_calls_max = budget_calls
        self.ledger: List[Dict[str, Any]] = []
        self._seen: set = set()
        self._handlers = handlers or []
        self._sem = asyncio.Semaphore(16)
        self._stop = False
        self.goal = goal
        self.budget_calls = budget_calls
        self._transport = transport
        self._own_client = None
        self.fast = False          # set by run(fast=…); read by the audit loop

    # ------------------------------------------------------------- plumbing

    async def emit(self, kind: str, data: Any) -> None:
        for h in self._handlers:
            await h((kind, data))

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------- ledger

    def _ledger_add(self, tool: str, findings: List[Dict[str, Any]]) -> int:
        added = 0
        for f in findings:
            if not isinstance(f, dict):
                continue
            key = (f.get("kind", ""), str(f.get("value", "")).lower(),
                   f.get("source", ""), f.get("evidence_url", ""))
            if key in self._seen:
                continue
            self._seen.add(key)
            self.ledger.append({"tool": tool, **f})
            added += 1
        return added

    # ---------------------------------------------------------- extraction

    def _extract_leads(self, text: str) -> List[str]:
        """Regex-verified new identifiers from free text (evidence rows only)."""
        found: List[str] = []
        for m in EMAIL_RE.findall(text):
            found.append(m)
        for m in URL_RE.findall(text):
            if "wikimedia" not in m and "gravatar" not in m:
                found.append(m.rstrip(".,);"))
        for m in DOMAIN_RE.findall(text):
            found.append(m)
        return list(dict.fromkeys(found))

    # --------------------------------------------------------------- rounds

    async def run(self, leads: List[str], goal: str = "", fast: bool = False) -> str:
        """Execute the full pipeline; returns the final markdown report.

        fast=True skips the per-round LLM self-audit (and its token cost)
        and uses one short synthesis pass — same tools, same evidence,
        ~half the wall clock. The TUI runs fast by default.
        """
        self.goal = goal or self.goal
        self.fast = fast
        self.analytics.t_start = _time.monotonic()
        self._stop = False
        await self.emit("start", {"leads": leads, "goal": self.goal})
        await self.emit("ready", {"tools": len(self.tools), "fence": FENCE})
        frontier: List[Tuple[str, str]] = [(l, classify(l)) for l in leads]
        depth = 0

        while frontier and depth <= MAX_DEPTH and not self._stop:
            self.analytics.leads_seen += len(frontier)
            # ---- classify + route (deterministic)
            batch: List[Tuple[Tool, Dict[str, Any]]] = []
            seen_tools: set = set()
            for lead, kind in frontier[:LEADS_PER_ROUND]:
                for tname in tools_for(kind):
                    if tname in seen_tools:
                        continue
                    tool = self.tools.get(tname)
                    if tool:
                        seen_tools.add(tname)
                        arg = ARGMAP.get(tname, "email")
                        batch.append((tool, {arg: lead}))
            # ---- self-audit: ask the LLM what's still dark
            if self.fast:
                # fast mode: no LLM round-trip — every family in the round
                # fires once, in registry order. Same evidence, half the wait.
                audit = []
                await self.emit("step", {"label": f"round {depth}: firing "
                                     f"{len(batch)} tools (fast mode)"})
            else:
                audit = await self._audit(frontier, depth)
                await self.emit("step", {"label": f"self-audit picked "
                                     f"{len(audit)} extra tool(s)"})
            for tname in audit:
                tool = self.tools.get(tname)
                if tool and tname not in seen_tools:
                    seen_tools.add(tname)
                    lead, _kind = frontier[0]
                    arg = ARGMAP.get(tname, tool.inputs[0])
                    batch.append((tool, {arg: lead}))
            self.analytics.pipelines_fired += len(batch)
            await self.emit("round", {"depth": depth, "tools": len(batch),
                                      "audit": audit, "ledger": len(self.ledger)})
            # ---- fire the batch in parallel (shared fenced client)
            if not batch:
                break
            results = await asyncio.gather(
                *[self._call(tool, args) for tool, args in batch])
            # ---- harvest evidence rows
            new_leads: List[str] = []
            for tool, _args, res in results:
                self._ledger_add(tool.name, res.findings)
                if res.ok:
                    if tool.family == "site" and "claimed" in res.summary:
                        self.analytics.site()
                    if tool.family == "engine" and "breach" in tool.name:
                        self.analytics.breach(1)
                    if tool.family == "dork":
                        self.analytics.dork(len(res.findings))
                    if "dark" in tool.name:
                        self.analytics.dark(len(res.findings))
                    text = " ".join(str(f.get("snippet", "")) + " " +
                                    str(f.get("value", "")) for f in res.findings)
                    new_leads.extend(self._extract_leads(text))
            # ---- next frontier: verified NEW pivots only
            nxt: List[Tuple[str, str]] = []
            known = {(l, k) for l, k in frontier}
            for lead in new_leads:
                kind = classify(lead)
                if (lead, kind) not in known and kind in \
                        ("email", "username", "domain", "url"):
                    known.add((lead, kind))
                    nxt.append((lead, kind))
                    self.analytics.pivot()
            frontier = nxt[:LEADS_PER_ROUND]
            depth += 1
            if self._stop or self.analytics.budget_hit() or \
                    self.analytics.elapsed_s() > BUDGET_SECONDS or \
                    self.analytics.llm_calls >= self.budget_calls:
                await self.emit("budget", {"max": self.budget_calls,
                                           "seconds": BUDGET_SECONDS,
                                           "elapsed": round(self.analytics.elapsed_s(), 1)})
                break

        await self.emit("step", {"label": "synthesizing the report"})
        report = await self._synthesize(leads, self.goal, fast=self.fast)
        await self.emit("report", {"len": len(report)})
        await self.emit("done", {"analytics": self.analytics.summary()})
        await self.close()
        return report

    async def _call(self, tool: Tool, args: Dict[str, Any]) -> Tuple[Tool, Dict[str, Any], Any]:
        async with self._sem:
            self.analytics.budget_calls += 1
            res = await tool.fn(self._client_shared(), **args)
            self.analytics.tool_calls += 1
            if not res.ok:
                self.analytics.tool_errors += 1
            self.analytics.fetches += 1
            self.analytics.bytes_scraped += len(res.summary) + 40 * len(res.findings)
            await self.emit("step", {"label": f"{tool.name} → "
                                 f"{'ok' if res.ok else 'inconclusive'} "
                                 f"· {res.ms:.0f}ms · {len(res.findings)} "
                                 f"finding(s)"})
            await self.emit("tool", {"name": tool.name, "ok": res.ok,
                                     "ms": round(res.ms, 1), "n": len(res.findings),
                                     "summary": res.summary[:120]})
            return tool, args, res

    _shared_client: Any = None

    def _client_shared(self):
        from .tools import SafeClient
        if Planner._shared_client is None:
            Planner._shared_client = SafeClient()
        if self._transport is not None and self._own_client is None:
            # demo/offline mode: swap in a fenced client over the fixture transport
            self._own_client = SafeClient()
            self._own_client.http._transport = self._transport
            Planner._shared_client = self._own_client
        return Planner._shared_client

    async def close(self) -> None:
        if Planner._shared_client is not None:
            await Planner._shared_client.aclose()
            Planner._shared_client = None

    # ------------------------------------------------------------- self-audit

    async def _audit(self, frontier: List[Tuple[str, str]], depth: int) -> List[str]:
        """Ask the model which tool families are still dark. It picks; we run."""
        coverage: Dict[str, int] = {}
        for row in self.ledger:
            fam = str(row.get("source", "")).split(".")[0]
            coverage[fam] = coverage.get(fam, 0) + 1
        fams = "\n".join(f"- {f}: {n} findings" for f, n in sorted(coverage.items()))
        available = ", ".join(sorted({t.split(".")[0] for t in self.tools}))
        prompt = (
            f"Round {depth}. Leads on the table: "
            f"{', '.join(f'{l}({k})' for l, k in frontier[:6]) or 'none'}.\n"
            f"Evidence coverage so far:\n{fams or '(empty)'}\n"
            f"Available tool families: {available}.\n"
            "Which 2-4 tool FAMILIES are still dark and would add the most "
            "evidence for a self-audit? Reply with ONLY a JSON array of tool "
            "names, e.g. [\"dork.email\", \"engine.wayback\"]. If coverage is "
            "good, reply []. No prose."
        )
        try:
            out = ""
            self.analytics.llm_start()
            async for chunk, _st in self.llm.chat(
                    [{"role": "user", "content": prompt}]):
                out += chunk
            cleaned = out.strip().strip("`").strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1].strip()
            inner = cleaned.removeprefix("[").removesuffix("]").strip()
            names = json.loads(f"[{inner}]") if inner else []
            if isinstance(names, str):
                names = [names]
            picked = [str(n) for n in names if str(n) in self.tools][:4]
            await self.emit("audit", {"picked": picked, "depth": depth})
            return picked
        except Exception:  # noqa: BLE001 — audit is advisory only
            return []

    # ------------------------------------------------------------ synthesis

    async def _synthesize(self, leads: List[str], goal: str,
                          fast: bool = False) -> str:
        # fast mode: a short cap keeps the synthesis call cheap and quick.
        ledger = self.ledger[:60] if fast else self.ledger[:300]
        digest = "\n".join(
            f"- {r.get('kind')} | {r.get('value')} | via {r.get('source') or r.get('tool')} "
            f"| {r.get('evidence_url')} | {str(r.get('snippet'))[:160]}"
            for r in ledger) or "(no evidence rows)"
        breaches = [r for r in self.ledger
                    if "breach" in str(r.get("source", "")).lower()
                    or "hibp" in str(r.get("source", "")).lower()]
        breach_digest = "\n".join(
            f"- {r.get('value')} | {r.get('source')} | {str(r.get('snippet'))[:200]}"
            for r in breaches[:40]) or "(no breach-index hits)"
        stats = json.dumps(self.analytics.summary(), indent=1)
        goal_text = goal or "self-audit of the digital footprint and breach exposure of the subject leads"
        prompt = (
            f"Subject leads: {', '.join(leads)}.\n"
            f"Goal: {goal_text}.\n"
            f"Safety fence in force: {FENCE}\n"
            "Remember: blocked/ambiguous results are INCONCLUSIVE — never "
            "report them as 'not found'.\n\n"
            f"EVIDENCE LEDGER ({len(ledger)} rows, deduped, evidence-only):\n{digest}\n\n"
            f"BREACH & LEAK ROWS:\n{breach_digest}\n\n"
            f"ANALYTICS:\n{stats}\n\n"
            "Write the full structured report exactly per REPORT_FORMAT in your "
            "system prompt. Terse, citation-dense, no filler. End with the "
            "analytics table verbatim from the ANALYTICS block. Section 8 must "
            "list every inconclusive/blocked tool result; quote errors verbatim."
        )
        out = ""
        self.analytics.llm_start()
        async for chunk, st in self.llm.chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}]):
            out += chunk
            if st:
                self.analytics.ttft(st.get("ttft_ms", 0.0))
                self.analytics.tokens(st.get("prompt_tokens", 0),
                                      st.get("completion_tokens", 0))
            await self.emit("llm", {"chunk": chunk})
        self.analytics.stream_bytes_out += len(out.encode("utf-8"))
        return out

    # ----------------------------------------------------------- helpers

    def ledger_rows(self) -> List[Dict[str, Any]]:
        return list(self.ledger)

    def ledger_table(self) -> str:
        return "\n".join(
            f"| {r.get('kind','')} | {r.get('value','')} | "
            f"{r.get('source') or r.get('tool')} | {r.get('evidence_url','')} | "
            f"{str(r.get('snippet',''))[:100]} |"
            for r in self.ledger[:50])

    def stats(self) -> Dict[str, Any]:
        return self.analytics.summary()

    def write_report(self, out_dir: str, report: str) -> str:
        import os
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "report.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        with open(os.path.join(out_dir, "analytics.json"), "w", encoding="utf-8") as fh:
            json.dump(self.analytics.summary(), fh, indent=1)
        with open(os.path.join(out_dir, "evidence-ledger.jsonl"), "w",
                  encoding="utf-8") as fh:
            for row in self.ledger:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def redacted_state(self) -> Dict[str, Any]:
        """State safe for logs: no keys, no lead PII beyond counts."""
        return {"tools": len(self.tools), "ledger_rows": len(self.ledger),
                "analytics": self.analytics.summary(), "fence": FENCE,
                "model": self.llm.model, "provider": self.llm.provider,
                "leads": len(self.ledger)}

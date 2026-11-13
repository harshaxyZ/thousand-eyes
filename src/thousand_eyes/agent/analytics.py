"""Run analytics: the operator's dashboard numbers.

Tracks: wall clock, TTFT (per LLM call + first token ever), tok/s, in/out
tokens, tool calls, fetches, bytes scraped, breaches found, sites claimed,
dork hits, dark-index hits, rate-limit events, errors. Renders as a table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Analytics:
    t_start: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    ttft_first_ms: float = 0.0
    ttft_min_ms: float = 0.0
    ttft_max_ms: float = 0.0
    ttft_sum_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    stream_bytes_out: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    fetches: int = 0
    bytes_scraped: int = 0
    breaches: int = 0
    sites_claimed: int = 0
    dork_hits: int = 0
    dark_hits: int = 0
    rate_limit_events: int = 0
    pipelines_fired: int = 0
    leads_seen: int = 0
    pivots: int = 0
    budget_calls: int = 0
    budget_calls_max: int = 400
    # NOTE: Planner overrides this from its budget_calls constructor arg.
    budget_seconds: float = 600.0

    # ------------------------------------------------------------- events

    def llm_start(self) -> None:
        self.llm_calls += 1

    def ttft(self, ms: float) -> None:
        if self.ttft_first_ms == 0.0:
            self.ttft_first_ms = ms
        self.ttft_min_ms = ms if self.ttft_min_ms == 0.0 else min(self.ttft_min_ms, ms)
        self.ttft_max_ms = max(self.ttft_max_ms, ms)
        self.ttft_sum_ms += ms

    def tokens(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def tool(self, ok: bool, fetches: int = 0, bytes_out: int = 0) -> None:
        self.tool_calls += 1
        if not ok:
            self.tool_errors += 1
        self.fetches += fetches
        self.bytes_scraped += bytes_out

    def breach(self, n: int = 1) -> None:
        self.breaches += n

    def site(self) -> None:
        self.sites_claimed += 1

    def dork(self, n: int = 1) -> None:
        self.dork_hits += n

    def dark(self, n: int = 1) -> None:
        self.dark_hits += n

    def rate_limited(self) -> None:
        self.rate_limit_events += 1

    def pipeline(self) -> None:
        self.pipelines_fired += 1

    def lead(self) -> None:
        self.leads_seen += 1

    def pivot(self) -> None:
        self.pivots += 1

    def budget_hit(self) -> bool:
        """True once the tool budget is spent (a pure check: the planner
        counts each tool call itself)."""
        return self.budget_calls >= self.budget_calls_max

    # -------------------------------------------------------------- report

    def elapsed_s(self) -> float:
        return time.monotonic() - self.t_start

    def avg_ttft_ms(self) -> float:
        return self.ttft_sum_ms / self.llm_calls if self.llm_calls else 0.0

    def tok_per_s(self, elapsed: float | None = None) -> float:
        e = elapsed if elapsed is not None else self.elapsed_s()
        return self.completion_tokens / e if e > 0 else 0.0

    def summary(self) -> Dict[str, Any]:
        e = self.elapsed_s()
        return {
            "wall_s": round(e, 1),
            "llm_calls": self.llm_calls,
            "ttft_first_ms": round(self.ttft_first_ms, 1),
            "ttft_avg_ms": round(self.avg_ttft_ms(), 1),
            "ttft_min_ms": round(self.ttft_min_ms, 1),
            "ttft_max_ms": round(self.ttft_max_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "tok_per_s": round(self.tok_per_s(e), 1),
            "stream_bytes_out_kb": round(self.stream_bytes_out / 1024, 1),
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "fetches": self.fetches,
            "data_scraped_mb": round(self.bytes_scraped / 1_000_000, 2),
            "breaches": self.breaches,
            "sites_claimed": self.sites_claimed,
            "dork_hits": self.dork_hits,
            "dark_index_hits": self.dark_hits,
            "rate_limit_events": self.rate_limit_events,
            "pipelines_fired": self.pipelines_fired,
            "leads_seen": self.leads_seen,
            "pivots": self.pivots,
            "budget_calls": self.budget_calls,
            "budget_calls_max": self.budget_calls_max,
        }

    def table(self) -> str:
        s = self.summary()
        rows = [
            ("wall clock", f"{s['wall_s']}s"),
            ("LLM calls", s["llm_calls"]),
            ("TTFT first/avg/min/max",
             f"{s['ttft_first_ms']} / {s['ttft_avg_ms']} / {s['ttft_min_ms']} / "
             f"{s['ttft_max_ms']} ms"),
            ("prompt / completion tokens", f"{s['prompt_tokens']} / {s['completion_tokens']}"),
            ("stream tok/s", s["tok_per_s"]),
            ("streamed out", f"{s['stream_bytes_out_kb']} KB"),
            ("tool calls / errors", f"{s['tool_calls']} / {s['tool_errors']}"),
            ("HTTP fetches", s["fetches"]),
            ("data scraped", f"{s['data_scraped_mb']} MB"),
            ("breaches confirmed", s["breaches"]),
            ("sites claimed", s["sites_claimed"]),
            ("dork hits", s["dork_hits"]),
            ("dark-index hits", s["dark_index_hits"]),
            ("rate-limit events", s["rate_limit_events"]),
            ("pipelines fired", s["pipelines_fired"]),
            ("leads / pivots", f"{s['leads_seen']} / {s['pivots']}"),
            ("tool budget used", f"{s['budget_calls']}/{s['budget_calls_max']}"),
        ]
        w = max(len(k) for k, _ in rows)
        return "\n".join(f"  {k:<{w}}  {v}" for k, v in rows)

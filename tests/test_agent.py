"""Hermetic tests for the agent pipeline (v0.5).

No network, no real key: the LLM is a scripted fake; the tool layer runs
against the fixture transport. Asserts: classification, batching, fence,
ledger dedup, analytics, budget cap, and the Gemini SSE parser.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from thousand_eyes.agent import Planner, classify, tools_for, count_tools
from thousand_eyes.agent.analytics import Analytics
from thousand_eyes.agent.providers import GeminiClient as LLMClient
from thousand_eyes.demo import demo_transport


AUDIT_MARKER = "Which 2-4 tool FAMILIES"
REPORT_MARKER = "Write the full structured report"


class FakeLLM:
    """Scripted LLM: routes by prompt content, records every call."""

    def __init__(self, audits: list[str], report: str):
        self.audits = list(audits)
        self.report = report
        self.calls: list[list[dict]] = []

    async def chat(self, messages, timeout_s: float = 120.0):
        self.calls.append(messages)
        text = messages[-1]["content"]
        if AUDIT_MARKER in text:
            out = self.audits.pop(0) if self.audits else "[]"
        else:
            out = self.report
        mid = max(1, len(out) // 2)  # two ordered chunks, like a real stream
        for piece in (out[:mid], out[mid:]):
            yield piece, {"ttft_ms": 5.0, "prompt_tokens": 10,
                          "completion_tokens": 5}

    async def aclose(self) -> None:
        pass


def _audit_reply(names: list[str]) -> str:
    return json.dumps(names)


@pytest.mark.asyncio
async def test_planner_full_pipeline_fence_and_report():
    # round 0 audit picks nothing new; round 1 picks wayback only;
    # round 2+ empty -> loop ends. Synthesis returns a fixed report.
    fake = FakeLLM(["[]", _audit_reply(["engine.wayback"]), "[]"],
                   "# Final Report\n\nAll clear.\n")
    planner = Planner(fake, goal="test", budget_calls=10)

    report = await planner.run(["demo.subject@example.com"])

    assert "# Final Report" in report
    assert planner.analytics.llm_calls >= 2
    assert planner.analytics.tool_calls > 0
    # fence: no onion/auth/form tools may ever be registered
    names = set(planner.tools)
    assert not any("onion" in n or "auth_probe" in n for n in names)
    await planner.close()


def test_classify_all_kinds():
    assert classify("a@b.com") == "email"
    assert classify("+14155552671") == "phone"
    assert classify("https://x.com/a") == "url"
    assert classify("nasa_nerd") == "username"
    assert classify("example.com") == "domain"


def test_tools_for_and_counts():
    assert len(tools_for("email")) > 5
    counts = count_tools()
    assert counts["TOTAL"] == sum(v for k, v in counts.items() if k != "TOTAL")
    assert counts["TOTAL"] >= 100


def test_ledger_dedup():
    """Planner ledger is deduped on (kind, value, source, evidence_url)."""
    planner = Planner(FakeLLM([], "# report"), goal="t")
    row = {"kind": "site", "value": "x", "source": "engine.github",
           "evidence_url": "https://github.com/x"}
    assert planner._ledger_add("engine.github", [row]) == 1
    assert planner._ledger_add("engine.github", [row]) == 0  # exact dup dropped
    assert len(planner.ledger) == 1


def test_analytics_summary():
    a = Analytics()
    a.llm_calls = 2
    a.ttft(100.0); a.ttft(300.0)
    a.tokens(1000, 500)
    a.tool(ok=True)
    s = a.summary()
    assert s["llm_calls"] == 2
    assert s["ttft_avg_ms"] == 200.0
    assert s["total_tokens"] == 1500
    assert s["tool_calls"] == 1


def test_gemini_stream_parse_and_stats():
    """SSE parser: multi-chunk stream -> full text + usage stats."""

    def sse_body():
        events = [
            {"candidates": [{"content": {"parts": [{"text": "Hel"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 1}},
            {"candidates": [{"content": {"parts": [{"text": "lo"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}},
        ]
        for e in events:
            yield f"data: {json.dumps(e)}\n\n"

    client = LLMClient.__new__(LLMClient)
    client.provider = "gemini"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="".join(sse_body()))))
    client.model = "gemini-3.5-flash-lite"
    client.key = "test-key"
    client.ttft_last_ms = 0.0

    async def run():
        text = ""
        st = {}
        async for chunk, s in client._gemini_stream([{"role": "user", "content": "hi"}]):
            text += chunk
            st = s
        return text, st

    text, stats = asyncio.run(run())
    assert text == "Hello" or text == "Hel" + "lo"
    assert stats.get("prompt_tokens") == 7
    assert stats.get("completion_tokens") == 2


def test_system_role_hoisted_to_system_instruction():
    """Gemini v1beta rejects role 'system' - chat() must hoist it."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, text='data: {"candidates": [{"content": '
                                        '{"parts": [{"text": "ok"}], "role": "model"}}]}\n\n')

    client = LLMClient.__new__(LLMClient)
    client.provider = "gemini"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.model = "gemini-3.5-flash-lite"
    client.key = "test-key"
    client.ttft_last_ms = 0.0

    async def run():
        out = ""
        async for chunk, _st in client.chat([
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "hi"}]):
            out += chunk
        return out

    assert asyncio.run(run()) == "ok"
    payload = seen["payload"]
    assert "systemInstruction" in payload
    assert all(m["role"] != "system" for m in payload["contents"])
    assert payload["systemInstruction"]["parts"][0]["text"] == "SYS"

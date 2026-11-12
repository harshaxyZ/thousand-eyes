"""LLM adapters: Anthropic (Claude) first, Google Gemini as fallback.

Default model is Claude (sonnet) because this is a Claude Code project;
if no Anthropic key is found we fall back to Gemini flash-lite (free tier).
Both adapters speak the same protocol: async stream of (chunk, stats) from
chat(messages), with ttft_ms / token accounting in stats.

No key, no run: the CLI says so loudly rather than silently degrading.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from .llm import LLMClient as _BaseLLM

# ------------------------------------------------------------------ models

ANTHROPIC_MODEL = os.getenv("THOUSAND_EYES_MODEL", "claude-sonnet-5")
CLAUDE_CANDIDATES = ("claude-sonnet-5", "claude-sonnet-4-5",
                     "claude-haiku-4-5-20251001")
GEMINI_FLASH_LITE = "gemini-3.5-flash-lite"     # free-tier default (2.5-flash-lite retired)
GEMINI_FLASH = "gemini-3.8-flash"
GEMINI_PRO = "gemini-2.5-pro"

ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
GEMINI_V1 = "https://generativelanguage.googleapis.com/v1beta"

CLAUDE_MODELS = CLAUDE_CANDIDATES
GEMINI_MODELS = (GEMINI_FLASH_LITE, GEMINI_FLASH, GEMINI_PRO)


def _key(*names: str) -> str:
    for var in names:
        if os.getenv(var):
            return os.environ[var]
    return ""


def discover_key() -> Tuple[str, str]:
    """Return (provider, key). Anthropic first, then Gemini, then key files."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "CLAUDE_API_KEY"):
        if os.getenv(var):
            return "anthropic", os.environ[var]
    for path in (os.path.expanduser("~/.gemini/api-key"),
                 os.path.expanduser("~/.gemini/key"),
                 os.path.expanduser("~/.config/gemini/api-key")):
        try:
            val = open(path).read().strip()
            if val:
                return "gemini", val
        except OSError:
            pass
    for var in ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY",
                "GOOGLE_GENERATIVE_API_KEY"):
        if os.getenv(var):
            return "gemini", os.environ[var]
    return "", ""


def best_model() -> str:
    """Model the CLI will use (anthropic wins whenever a key exists)."""
    provider, _ = discover_key()
    return ANTHROPIC_MODEL if provider == "anthropic" else GEMINI_FLASH_LITE


def client_for(model: Optional[str] = None) -> _BaseLLM:
    """Build the right client. Claude preferred, Gemini as fallback."""
    provider, key = discover_key()
    if model:
        if model.startswith("claude") or model.startswith("gpt"):
            return ClaudeClient(key or os.getenv("ANTHROPIC_API_KEY", ""), model)
        return GeminiClient(key or os.getenv("GEMINI_API_KEY", ""), model)
    if provider == "anthropic":
        return ClaudeClient(key, ANTHROPIC_MODEL)
    if provider == "gemini":
        return GeminiClient(key, GEMINI_FLASH_LITE)
    raise RuntimeError(
        "no LLM key found: set ANTHROPIC_API_KEY (claude) or GEMINI_API_KEY "
        "(free tier) - without one, use --demo for the offline replay")


# ------------------------------------------------------------------ claude


class ClaudeClient(_BaseLLM):
    """Streaming Claude chat: SSE events, TTFT, token accounting."""

    provider = "anthropic"

    def __init__(self, key: str, model: str = ANTHROPIC_MODEL) -> None:
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.key = key
        self.model = model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))
        self.ttft_last_ms = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: List[Dict[str, str]]) -> AsyncIterator[Tuple[str, Dict]]:
        system = [m["content"] for m in messages if m["role"] == "system"]
        convo = [m for m in messages if m["role"] != "system"]
        candidates = [self.model] if self.model not in CLAUDE_CANDIDATES else \
            [m for m in CLAUDE_CANDIDATES if m == self.model] + \
            [m for m in CLAUDE_CANDIDATES if m != self.model]
        last_err: Exception = RuntimeError("no usable claude model")
        for model in candidates:
            try:
                async for chunk, stats in self._stream(messages, convo, system, model):
                    yield chunk, stats
                return
            except RuntimeError as e:
                msg = str(e)
                if "model" not in msg.lower() and "not_found" not in msg.lower() \
                        and "404" not in msg and "400" not in msg:
                    raise
                last_err = e
                continue
        raise last_err

    async def _stream(self, messages, convo, system, model) -> AsyncIterator[Tuple[str, Dict]]:
        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": 2048,
            "system": "\n\n".join(system),
            "messages": [
                {"role": "user" if m["role"] == "user" else "assistant",
                 "content": m["content"]}
                for m in convo
            ],
            "stream": True,
        }
        t0 = time.monotonic()
        first = True
        stats: Dict[str, Any] = {}
        async with self._http.stream(
                "POST", ANTHROPIC_API,
                json=payload,
                headers={"x-api-key": self.key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"}) as r:
            if r.status_code == 429:
                raise RuntimeError("anthropic rate-limited (quota)")
            if r.status_code >= 400:
                body = (await r.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"anthropic {r.status_code}: {body}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type", "")
                if et == "content_block_delta" and "delta" in ev:
                    txt = ev["delta"].get("text", "")
                    if txt:
                        if first:
                            stats["ttft_ms"] = (time.monotonic() - t0) * 1000
                            first = False
                        yield txt, stats
                elif et == "message_start":
                    usage = ev.get("message", {}).get("usage", {})
                    stats["prompt_tokens"] = usage.get("input_tokens", 0)
                elif et == "message_delta":
                    usage = ev.get("usage", {})
                    stats["completion_tokens"] = usage.get("output_tokens", 0)
                elif et == "message_stop":
                    stats["done"] = True


# ------------------------------------------------------------------ gemini


class GeminiClient(_BaseLLM):
    """Streaming Gemini chat (kept from v0.5 - free-tier fallback)."""

    provider = "gemini"

    def __init__(self, key: str, model: str = GEMINI_FLASH_LITE) -> None:
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.key = key
        self.model = model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
        self.ttft_last_ms = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: List[Dict[str, str]]) -> AsyncIterator[Tuple[str, Dict]]:
        async for chunk, stats in self._gemini_stream(messages):
            yield chunk, stats

    async def _gemini_stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[Tuple[str, Dict]]:
        # Gemini v1beta has no "system" role: hoist it into systemInstruction.
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else m["role"]
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        payload: Dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": p} for p in system_parts]}
        url = f"{GEMINI_V1}/models/{self.model}:streamGenerateContent"  # v1beta/models/<id>:...
        params = {"alt": "sse", "key": self.key}
        t0 = time.monotonic()
        first = True
        stats: Dict[str, Any] = {}
        async with self._http.stream("POST", url, params=params,
                                     json=payload,
                                     headers={"Content-Type": "application/json"}) as r:
            if r.status_code == 429:
                raise RuntimeError("gemini rate-limited (free tier quota)")
            if r.status_code >= 400:
                body = (await r.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"gemini {r.status_code}: {body}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload_line = line[5:].strip()
                if not payload_line or payload_line == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload_line)
                except json.JSONDecodeError:
                    continue
                for cand in obj.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        txt = part.get("text", "")
                        if txt:
                            if first:
                                stats["ttft_ms"] = (time.monotonic() - t0) * 1000
                                first = False
                            yield txt, stats
                usage = obj.get("usageMetadata") or {}
                if usage:
                    stats["prompt_tokens"] = usage.get("promptTokenCount", 0)
                    stats["completion_tokens"] = usage.get("candidatesTokenCount", 0)
                    stats["total_tokens"] = usage.get("totalTokenCount", 0)


# backwards-compatible name: the default provider client is Claude
LLMClient = ClaudeClient

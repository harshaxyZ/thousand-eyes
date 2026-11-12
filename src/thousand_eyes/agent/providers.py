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
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

#: preferred local model names, best first - whatever ollama actually has
#: installed wins (see local_models()).
LOCAL_MODEL_PREFERENCE = ("qwen2.5", "llama3.2", "llama3.1", "mistral",
                          "phi3", "gemma2", "llama3")

CLAUDE_MODELS = CLAUDE_CANDIDATES
GEMINI_MODELS = (GEMINI_FLASH_LITE, GEMINI_FLASH, GEMINI_PRO)


def _key(*names: str) -> str:
    for var in names:
        if os.getenv(var):
            return os.environ[var]
    return ""


def discover_key() -> Tuple[str, str]:
    """Return (provider, key). Order: the /setup config file (the user's
    explicit choice), then env vars (anthropic, then gemini), then the
    legacy key files."""
    cfg = os.path.expanduser("~/.thousand-eyes/config.json")
    try:
        with open(cfg, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("key"):
            return str(data.get("provider") or "gemini"), str(data["key"])
    except (OSError, ValueError):
        pass
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


def local_models() -> list[str]:
    """Models a local ollama server has installed (empty when it is not
    running). This is the only completely free LLM path: no key, no
    account, no network."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        data = r.json()
    except Exception:      # noqa: BLE001 - no ollama, no problem
        return []
    names = [str(m.get("name") or "") for m in (data.get("models") or [])]
    names = [n for n in names if n]
    # prefer the models we know behave well for this job
    for preferred in LOCAL_MODEL_PREFERENCE:
        for name in names:
            if name.lower().startswith(preferred):
                return [name]
    return names


def best_local_model() -> str:
    models = local_models()
    return models[0] if models else ""


def client_for(model: Optional[str] = None) -> _BaseLLM:
    """Build the right client. Claude preferred, Gemini as fallback, and a
    local ollama model when neither key is available (the free path)."""
    provider, key = discover_key()
    if model:
        if model.startswith("claude") or model.startswith("gpt"):
            return ClaudeClient(key or os.getenv("ANTHROPIC_API_KEY", ""), model)
        if model.startswith(("llama", "qwen", "mistral", "phi", "gemma")):
            return OllamaClient(model)
        return GeminiClient(key or os.getenv("GEMINI_API_KEY", ""), model)
    if provider == "anthropic":
        return ClaudeClient(key, ANTHROPIC_MODEL)
    if provider == "gemini":
        return GeminiClient(key, GEMINI_FLASH_LITE)
    local = best_local_model()
    if local:
        return OllamaClient(local)
    raise RuntimeError(
        "no LLM found: run `thousand-eyes --setup` (gemini free tier or claude), "
        "or start a local ollama server for a keyless, offline model - the "
        "audit itself never needs an LLM")


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
                if "free tier" in body.lower() and "not available" in body.lower():
                    raise RuntimeError(
                        "this gemini key's project has no free-tier access "
                        "(google disabled it for this project). enable it at "
                        "aistudio.google.com/billing, or run `thousand-eyes "
                        "--setup claude <key>`, or start a local ollama "
                        "server for a keyless free model. the audit itself "
                        "never needs an LLM")
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


# ------------------------------------------------------------------ ollama


class OllamaClient(_BaseLLM):
    """A LOCAL model through ollama - completely free, no key, no account,
    no data leaving the machine. The best free path when a hosted free tier
    is unavailable."""

    provider = "ollama"

    def __init__(self, model: str = "qwen2.5") -> None:
        self.model = model or "qwen2.5"
        self.base = OLLAMA_URL
        self.ttft_last_ms = 0.0

    async def aclose(self) -> None:
        return None

    async def chat(self, messages: List[Dict[str, str]]) -> AsyncIterator[Tuple[str, Dict]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
        payload: Dict[str, Any] = {"model": self.model, "messages": convo,
                                   "stream": False}
        if system:
            payload["system"] = system
        t0 = time.monotonic()
        stats: Dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as http:
            try:
                r = await http.post(f"{self.base}/api/chat", json=payload)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"ollama unreachable at {self.base}: {exc}") from exc
            if r.status_code >= 400:
                body = r.text[:200]
                raise RuntimeError(f"ollama {r.status_code}: {body}")
            data = r.json()
        stats["ttft_ms"] = (time.monotonic() - t0) * 1000
        text = ((data.get("message") or {}).get("content") or "").strip()
        if text:
            yield text, stats
        if data.get("eval_count"):
            stats["completion_tokens"] = int(data.get("eval_count") or 0)


# backwards-compatible name: the default provider client is Claude
LLMClient = ClaudeClient

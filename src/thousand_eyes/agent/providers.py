"""Gemini-only LLM adapter.

Flash-lite models by default (gemini-3.5-flash-lite) so a free-tier key
covers the whole audit: high usage ceiling, no cost.

No key, no run: the CLI refuses loudly rather than silently degrading.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from .llm import LLMClient as _BaseLLM

FLASH_LITE = "gemini-3.5-flash-lite"   # free-tier default (2.5-flash-lite retired)
FLASH = "gemini-3.8-flash"
PRO = "gemini-2.5-pro"

GEMINI_V1 = "https://generativelanguage.googleapis.com/v1beta"


def discover_key() -> Tuple[str, str]:
    """Return (provider, key). Scans env + the usual free-key files."""
    for var in ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_API_KEY"):
        if os.getenv(var):
            return "gemini", os.environ[var]
    for path in (os.path.expanduser("~/.gemini/api-key"),
                 os.path.expanduser("~/.gemini/key"),
                 os.path.expanduser("~/.config/gemini/api-key")):
        try:
            val = open(path).read().strip()
            if val:
                return "gemini", val
        except OSError:
            pass
    return "", ""


class LLMClient(_BaseLLM):
    """Streaming chat client with TTFT + token accounting."""

    def __init__(self, provider: str, key: str, model: str = FLASH_LITE) -> None:
        self.provider = provider
        self.key = key
        self.model = model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
        self.ttft_last_ms = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------- gemini

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
        t0 = __import__("time").monotonic()
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
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for cand in obj.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        text = part.get("text", "")
                        if text:
                            if first:
                                self.ttft_last_ms = (__import__("time").monotonic() - t0) * 1000
                                first = False
                            stats["ttft_ms"] = self.ttft_last_ms
                            yield text, stats
                meta = obj.get("usageMetadata") or {}
                if meta:
                    stats.update({"prompt_tokens": meta.get("promptTokenCount", 0),
                                  "completion_tokens": meta.get("candidatesTokenCount", 0)})

def best_model() -> str:
    """Flash-lite by default — free tier, high usage ceiling."""
    return os.getenv("THOUSAND_EYES_MODEL") or FLASH_LITE


def client_for(model: Optional[str] = None) -> LLMClient:
    provider, key = discover_key()
    if not provider:
        raise RuntimeError(
            "no API key found: put a Gemini key in GEMINI_API_KEY (or "
            "~/.gemini/api-key). Flash-lite models keep you on the free tier.")
    return LLMClient(provider, key, model or best_model())


_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{30,}")


def scan_text_for_gemini_key(text: str) -> Optional[str]:
    m = _KEY_RE.search(text)
    return m.group(0) if m else None

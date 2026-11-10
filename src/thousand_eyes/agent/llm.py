"""Thin protocol types for LLM clients (see providers.py for implementations)."""

from typing import AsyncIterator, Dict, List, Tuple


class LLMClient:
    """Protocol: async iterator of (chunk, stats) from chat(messages).

    stats carries ttft_ms / prompt_tokens / completion_tokens as they arrive.
    Concrete classes live in providers.py (Gemini / OpenRouter / local).
    """

    provider: str = "base"
    model: str = "unknown"

    async def chat(self, messages: List[Dict[str, str]]) -> AsyncIterator[Tuple[str, Dict]]:  # pragma: no cover - protocol
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - protocol
        pass

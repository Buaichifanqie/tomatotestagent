"""LLM Provider wrapper that tracks token usage.

Wraps any LLM provider and records token consumption
into a TokenTracker after each API call.
"""
from __future__ import annotations

from typing import Any

from testagent.common.token_tracker import TokenTracker


class TrackingLLMProvider:
    """Wraps an LLM provider to automatically track token usage.

    Usage:
        tracker = TokenTracker()
        provider = LLMProviderFactory.create(settings)
        tracked = TrackingLLMProvider(provider, tracker, category="llm")
        # Use tracked like a normal provider
        response = await tracked.chat(system="...", messages=[...])
        # Token usage is automatically recorded in tracker
    """

    def __init__(self, inner: Any, tracker: TokenTracker, category: str = "llm") -> None:
        self._inner = inner
        self._tracker = tracker
        self._category = category

    async def chat(self, **kwargs: Any) -> Any:
        """Delegate to inner provider and record token usage."""
        result = await self._inner.chat(**kwargs)

        # Extract token usage from response
        if hasattr(result, "usage") and isinstance(result.usage, dict):
            pt = result.usage.get("input_tokens", 0) or result.usage.get("prompt_tokens", 0)
            ct = result.usage.get("output_tokens", 0) or result.usage.get("completion_tokens", 0)
            tt = result.usage.get("total_tokens", 0)
            self._tracker.record(
                category=self._category,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
            )
            if tt > 0:
                _detail = f"{pt}↑ {ct}↓ = {tt}" if pt and ct else str(tt)
                print(f"      \033[36m[LLM tokens] {_detail}\033[0m")

        return result

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to inner provider."""
        return getattr(self._inner, name)

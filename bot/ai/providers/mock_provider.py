"""
Mock provider — returns a deterministic APPROVE decision.
Use during local development so you don't burn API credits.
"""
import json

from bot.ai.providers.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """Returns a canned APPROVE response. Safe for local dev and unit tests."""

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps({
            "decision": "APPROVE",
            "confidence": 0.92,
            "reasoning": "Mock provider: all answers look fine for development testing.",
            "follow_up_required": False,
        })

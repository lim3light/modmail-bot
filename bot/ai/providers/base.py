"""
Abstract base for LLM providers. Swap provider by changing config — zero code changes.
"""
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """All LLM providers implement this interface. One method, one contract."""

    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a prompt and return the raw text response.
        Raises: LLMProviderError on unrecoverable failures.
        """
        ...


class LLMProviderError(Exception):
    """Raised when the LLM provider returns an unrecoverable error."""
    pass

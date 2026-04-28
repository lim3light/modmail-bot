"""
Anthropic provider — wraps the async Anthropic client.
"""
import anthropic

from bot.ai.providers.base import BaseLLMProvider, LLMProviderError


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str, timeout: int = 30) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._timeout = timeout

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                timeout=self._timeout,
            )
            return response.content[0].text
        except anthropic.APIError as e:
            raise LLMProviderError(f"Anthropic API error: {e}") from e

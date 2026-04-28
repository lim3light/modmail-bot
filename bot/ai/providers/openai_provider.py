"""
OpenAI provider — wraps the async OpenAI client.
"""
import openai

from bot.ai.providers.base import BaseLLMProvider, LLMProviderError


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str, timeout: int = 30) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout)
        self._model = model

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except openai.APIError as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e

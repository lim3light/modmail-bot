"""
AIJudgeService — prompt construction, LLM call, retry logic, schema validation.
This is the only entry point into the AI pipeline.
"""
from __future__ import annotations

import asyncio
import json

import structlog
from pydantic import ValidationError

from bot.ai.providers.base import BaseLLMProvider, LLMProviderError
from bot.ai.schemas import AIDecision, VerificationContext
from bot.core.models import ThreadState

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
You are a moderation assistant for a Discord server. Your role is to evaluate
a new member's verification answers and decide their access level.

Server context: {server_context}

Be consistent, fair, and conservative. When in doubt, choose VISITOR or flag
for follow-up rather than making a confident wrong call.

Your output MUST be a single valid JSON object with NO additional text,
markdown, or code fences. Use exactly this schema:
{{"decision": "APPROVE | VISITOR | REJECT", "confidence": 0.00, "reasoning": "...", "follow_up_required": false}}

Decision criteria:
- APPROVE: member clearly fits the community, answers are coherent and genuine.
  Only approve with confidence ≥ 0.80.
- VISITOR: member is uncertain or answers are ambiguous. Use for confidence 0.50–0.79.
  This gives limited access, not a ban.
- REJECT: clear red flags — spam patterns, evasion, or harmful intent.
  Only reject with confidence ≥ 0.75. If unsure, use VISITOR.

Set follow_up_required to true only if a single clarifying question would
meaningfully change your decision. Do not set it if you already have enough
information for a confident assessment.

Reasoning: 1–3 sentences max. Be factual, not moralistic.
"""

USER_PROMPT = """\
This is verification round {round_number} of {max_rounds}.

{formatted_qa}

Evaluate this member's answers and return your JSON decision.
"""


class AIJudgeService:
    RETRY_DELAYS = [1.0, 3.0, 10.0]   # seconds between attempts

    def __init__(
        self,
        provider: BaseLLMProvider,
        server_context: str,
        max_retries: int = 3,
    ) -> None:
        self._provider = provider
        self._server_context = server_context
        self._max_retries = min(max_retries, len(self.RETRY_DELAYS))

    async def evaluate(self, state: ThreadState) -> AIDecision:
        """
        Evaluate a thread's verification answers.
        Raises: LLMProviderError after all retries exhausted.
                ValidationError if LLM output is unrecoverable garbage.
        """
        ctx = VerificationContext(
            thread_id=state.thread_id,
            user_id=state.user_id,
            guild_id=state.guild_id,
            question_round=state.question_round,
            max_rounds=3,
            server_context=self._server_context,
            formatted_qa=state.format_qa_for_prompt(),
        )

        system = SYSTEM_PROMPT.format(server_context=self._server_context)
        user = USER_PROMPT.format(
            round_number=ctx.question_round,
            max_rounds=ctx.max_rounds,
            formatted_qa=ctx.formatted_qa,
        )

        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                raw = await self._provider.complete(system, user)
                decision = self._parse(raw, state.thread_id)
                log.info(
                    "ai_judge_success",
                    thread_id=state.thread_id,
                    attempt=attempt,
                    decision=decision.decision,
                    confidence=decision.confidence,
                )
                return decision

            except (LLMProviderError, asyncio.TimeoutError) as e:
                last_error = e
                log.warning(
                    "ai_judge_retry",
                    thread_id=state.thread_id,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self.RETRY_DELAYS[attempt])

            except _SchemaError as e:
                # Schema errors are not retryable — LLM is confabulating.
                log.error(
                    "ai_judge_schema_failure",
                    thread_id=state.thread_id,
                    error=str(e),
                )
                raise LLMProviderError("LLM output failed schema validation") from e

        raise LLMProviderError(
            f"LLM failed after {self._max_retries} attempts: {last_error}"
        )

    def _parse(self, raw: str, thread_id: str) -> AIDecision:
        """Parse and validate raw LLM text → AIDecision. Raises _SchemaError on failure."""
        try:
            # Strip markdown fences the LLM may add despite instructions
            cleaned = raw.strip()
            for fence in ("```json", "```"):
                cleaned = cleaned.removeprefix(fence).removesuffix(fence).strip()

            data = json.loads(cleaned)
            return AIDecision.model_validate(data)

        except json.JSONDecodeError as e:
            raise _SchemaError(f"Invalid JSON from LLM: {e}. Raw: {raw[:200]}") from e
        except ValidationError as e:
            raise _SchemaError(f"Schema mismatch: {e}. Raw: {raw[:200]}") from e


class _SchemaError(Exception):
    """Internal — LLM output doesn't match the required schema. Not retryable."""
    pass

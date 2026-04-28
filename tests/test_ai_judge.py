"""
Unit tests for the AI judge pipeline.
All LLM calls are mocked — no API keys needed to run these.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.ai.judge import AIJudgeService
from bot.ai.schemas import AIDecision
from bot.ai.providers.base import LLMProviderError
from bot.core.models import AIMode, QAPair, ThreadState, ThreadStatus


def _make_state(**kwargs) -> ThreadState:
    defaults = dict(
        thread_id="test-thread-123",
        user_id=12345,
        guild_id=99999,
        channel_id=77777,
        status=ThreadStatus.AI_PROCESSING,
        ai_mode=AIMode.ENABLED,
        question_round=1,
        qa_history=[
            QAPair(
                question="How did you find us?",
                answer="Through a friend recommendation.",
            ),
            QAPair(
                question="Tell us about yourself.",
                answer="I'm a software developer interested in your community topics.",
            ),
        ],
    )
    defaults.update(kwargs)
    return ThreadState(**defaults)


def _make_provider(response: str) -> AsyncMock:
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=response)
    return provider


# ── Happy path ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_returns_valid_approve_decision():
    response = json.dumps({
        "decision": "APPROVE",
        "confidence": 0.91,
        "reasoning": "Clear and genuine answers. Member is a good fit.",
        "follow_up_required": False,
    })
    provider = _make_provider(response)
    judge = AIJudgeService(provider=provider, server_context="Test server.")

    result = await judge.evaluate(_make_state())

    assert isinstance(result, AIDecision)
    assert result.decision == "APPROVE"
    assert result.confidence == pytest.approx(0.91)
    assert result.follow_up_required is False


@pytest.mark.asyncio
async def test_evaluate_strips_markdown_fences():
    """LLMs often wrap JSON in ```json ... ``` despite instructions."""
    response = "```json\n" + json.dumps({
        "decision": "VISITOR",
        "confidence": 0.65,
        "reasoning": "Answers are vague but not suspicious.",
        "follow_up_required": False,
    }) + "\n```"

    provider = _make_provider(response)
    judge = AIJudgeService(provider=provider, server_context="Test server.")

    result = await judge.evaluate(_make_state())
    assert result.decision == "VISITOR"


@pytest.mark.asyncio
async def test_evaluate_requests_followup():
    response = json.dumps({
        "decision": "VISITOR",
        "confidence": 0.55,
        "reasoning": "First answer was evasive. Need more context.",
        "follow_up_required": True,
    })
    provider = _make_provider(response)
    judge = AIJudgeService(provider=provider, server_context="Test server.")

    result = await judge.evaluate(_make_state())
    assert result.follow_up_required is True


# ── Failure paths ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_retries_on_provider_error():
    good_response = json.dumps({
        "decision": "APPROVE",
        "confidence": 0.88,
        "reasoning": "Good candidate.",
        "follow_up_required": False,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[LLMProviderError("Timeout"), good_response]
    )
    judge = AIJudgeService(
        provider=provider,
        server_context="Test server.",
        max_retries=3,
    )
    # Patch sleep so test doesn't actually wait
    import bot.ai.judge as judge_module
    judge_module.asyncio = MagicMock(sleep=AsyncMock())

    result = await judge.evaluate(_make_state())
    assert result.decision == "APPROVE"
    assert provider.complete.call_count == 2


@pytest.mark.asyncio
async def test_evaluate_raises_after_all_retries_exhausted():
    provider = _make_provider("irrelevant")
    provider.complete = AsyncMock(side_effect=LLMProviderError("API down"))

    import bot.ai.judge as judge_module
    judge_module.asyncio = MagicMock(sleep=AsyncMock())

    judge = AIJudgeService(provider=provider, server_context="Test.", max_retries=2)
    with pytest.raises(LLMProviderError):
        await judge.evaluate(_make_state())


@pytest.mark.asyncio
async def test_evaluate_raises_on_invalid_json():
    provider = _make_provider("This is not JSON at all.")
    judge = AIJudgeService(provider=provider, server_context="Test server.")

    with pytest.raises(LLMProviderError, match="schema validation"):
        await judge.evaluate(_make_state())


@pytest.mark.asyncio
async def test_evaluate_raises_on_schema_mismatch():
    # Valid JSON but wrong shape
    response = json.dumps({"verdict": "yes", "score": 0.9})
    provider = _make_provider(response)
    judge = AIJudgeService(provider=provider, server_context="Test server.")

    with pytest.raises(LLMProviderError, match="schema validation"):
        await judge.evaluate(_make_state())


# ── Threshold checks ───────────────────────────────────────────────────────────

def test_decision_meets_threshold_approve():
    decision = AIDecision(
        decision="APPROVE", confidence=0.85,
        reasoning="Strong candidate.", follow_up_required=False
    )
    thresholds = {"APPROVE": 0.80, "VISITOR": 0.50, "REJECT": 0.75}
    assert decision.meets_threshold(thresholds) is True


def test_decision_does_not_meet_threshold_approve():
    decision = AIDecision(
        decision="APPROVE", confidence=0.75,
        reasoning="Borderline candidate.", follow_up_required=False
    )
    thresholds = {"APPROVE": 0.80, "VISITOR": 0.50, "REJECT": 0.75}
    assert decision.meets_threshold(thresholds) is False

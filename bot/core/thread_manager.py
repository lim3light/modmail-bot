"""
ThreadManager — the central orchestrator. Owns thread lifecycle, routes events
to AI pipeline or action executor, enforces state machine transitions.
"""
from __future__ import annotations

import structlog

from bot.config import Settings
from bot.core.events import (
    AIDecisionReadyEvent,
    AIJudgeRequestedEvent,
    HumanReviewRequiredEvent,
    ModOverrideEvent,
)
from bot.core.models import AIMode, QAPair, ThreadState, ThreadStatus
from bot.persistence.repositories.thread_repo import ThreadRepository

log = structlog.get_logger(__name__)

# Default verification questions. In production, load these from DB per-guild.
DEFAULT_QUESTIONS: list[str] = [
    "How did you find this server, and what drew you to join?",
    "Tell us a bit about yourself and what you're hoping to get from this community.",
    "Have you read the server rules? Is there anything you'd like to ask about them?",
]

FOLLOWUP_QUESTIONS: list[str] = [
    "Your previous answer was a bit brief — could you expand on that?",
    "Can you tell us a little more about your background or interests?",
]


class ThreadManager:
    def __init__(
        self,
        settings: Settings,
        thread_repo: ThreadRepository,
        ai_judge,       # AIJudgeService — typed loosely to avoid circular import
        action_executor,
    ) -> None:
        self.settings = settings
        self.repo = thread_repo
        self.ai_judge = ai_judge
        self.executor = action_executor

    # ── Thread creation ────────────────────────────────────────────────────────

    async def open_thread(
        self,
        user_id: int,
        guild_id: int,
        channel_id: int,
        ai_mode_enabled: bool = False,
    ) -> ThreadState:
        import uuid
        thread_id = str(uuid.uuid4())

        state = ThreadState(
            thread_id=thread_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            status=ThreadStatus.OPEN,
            ai_mode=AIMode.ENABLED if ai_mode_enabled else AIMode.DISABLED,
        )
        await self.repo.save(state)
        log.info("thread_opened", thread_id=thread_id, user_id=user_id, ai_mode=state.ai_mode)
        return state

    # ── Verification flow ──────────────────────────────────────────────────────

    async def start_verification(self, thread_id: str) -> list[QAPair]:
        """Send initial question round. Returns the QA pairs (questions only at this point)."""
        state = await self.repo.get(thread_id)
        if state is None:
            raise ValueError(f"Thread {thread_id} not found")

        questions = DEFAULT_QUESTIONS
        for q in questions:
            state.add_question(q)

        state.question_round = 1
        state.transition(ThreadStatus.AWAITING_ANSWER)
        await self.repo.save(state)

        log.info("verification_started", thread_id=thread_id, num_questions=len(questions))
        return state.qa_history

    async def receive_answer(self, thread_id: str, answer: str) -> None:
        """Record a user answer. If all questions answered, trigger next step."""
        state = await self.repo.get(thread_id)
        if state is None or not state.is_awaiting_input:
            return

        state.record_answer(answer)

        all_answered = len(state.unanswered_questions) == 0

        if all_answered:
            if state.ai_active:
                state.transition(ThreadStatus.AI_PROCESSING)
                await self.repo.save(state)
                await self._run_ai_evaluation(state)
            else:
                # AI disabled — park in human review
                state.transition(ThreadStatus.HUMAN_REVIEW)
                await self.repo.save(state)
                log.info("thread_awaiting_human_review", thread_id=thread_id)
        else:
            await self.repo.save(state)

    # ── AI evaluation ──────────────────────────────────────────────────────────

    async def _run_ai_evaluation(self, state: ThreadState) -> None:
        log.info("ai_evaluation_starting", thread_id=state.thread_id, round=state.question_round)

        try:
            decision = await self.ai_judge.evaluate(state)
        except Exception as e:
            log.error("ai_evaluation_failed", thread_id=state.thread_id, error=str(e))
            await self._escalate_to_human(state, reason="llm_failure")
            return

        # Re-fetch in case state changed during async LLM call
        state = await self.repo.get(state.thread_id)
        if state is None or state.ai_mode == AIMode.HUMAN_OVERRIDE:
            return

        state.set_ai_decision(decision.decision, decision.confidence, decision.reasoning)

        # Request more info if LLM asks for it and we have rounds left
        if decision.follow_up_required and state.can_ask_followup:
            await self._send_followup(state)
            return

        # Check confidence thresholds
        threshold = self.settings.confidence_thresholds[decision.decision]
        auto_executable = decision.confidence >= threshold

        if not auto_executable:
            log.info(
                "ai_confidence_below_threshold",
                thread_id=state.thread_id,
                decision=decision.decision,
                confidence=decision.confidence,
                threshold=threshold,
            )
            await self._escalate_to_human(state, reason="low_confidence")
            return

        # Execute automatically
        state.transition(ThreadStatus.CLOSED)
        await self.repo.save(state)
        await self.executor.execute(state, decision)

        log.info(
            "ai_decision_executed",
            thread_id=state.thread_id,
            decision=decision.decision,
            confidence=decision.confidence,
        )

    async def _send_followup(self, state: ThreadState) -> None:
        if state.question_round > len(FOLLOWUP_QUESTIONS):
            await self._escalate_to_human(state, reason="max_rounds_exceeded")
            return

        followup_q = FOLLOWUP_QUESTIONS[state.question_round - 1]
        state.add_question(followup_q)
        state.question_round += 1
        state.transition(ThreadStatus.AWAITING_FOLLOWUP)
        await self.repo.save(state)

        log.info("followup_question_sent", thread_id=state.thread_id, round=state.question_round)

    async def _escalate_to_human(self, state: ThreadState, reason: str) -> None:
        state.transition(ThreadStatus.HUMAN_REVIEW)
        await self.repo.save(state)
        log.info("escalated_to_human", thread_id=state.thread_id, reason=reason)

    # ── Mod override ───────────────────────────────────────────────────────────

    async def apply_mod_override(
        self,
        thread_id: str,
        mod_id: int,
        decision: str,
    ) -> None:
        state = await self.repo.get(thread_id)
        if state is None:
            raise ValueError(f"Thread {thread_id} not found")

        original = state.ai_decision or "none"
        state.ai_mode = AIMode.HUMAN_OVERRIDE
        state.transition(ThreadStatus.CLOSED)
        await self.repo.save(state)

        # Build a synthetic decision object for the executor
        from bot.ai.schemas import AIDecision
        override = AIDecision(
            decision=decision,
            confidence=1.0,
            reasoning=f"Manual override by mod {mod_id}",
            follow_up_required=False,
        )
        await self.executor.execute(state, override)

        log.info(
            "mod_override_applied",
            thread_id=thread_id,
            mod_id=mod_id,
            decision=decision,
            original_ai_decision=original,
        )

    # ── Toggle AI mode ─────────────────────────────────────────────────────────

    async def set_ai_mode(
        self, thread_id: str, enabled: bool, mod_id: int
    ) -> None:
        state = await self.repo.get(thread_id)
        if state is None:
            raise ValueError(f"Thread {thread_id} not found")

        state.ai_mode = AIMode.ENABLED if enabled else AIMode.DISABLED
        await self.repo.save(state)
        log.info(
            "ai_mode_toggled",
            thread_id=thread_id,
            enabled=enabled,
            by_mod=mod_id,
        )

    async def close_thread(self, thread_id: str, closed_by: int) -> None:
        state = await self.repo.get(thread_id)
        if state is None:
            return
        state.transition(ThreadStatus.CLOSED)
        await self.repo.save(state)
        log.info("thread_closed", thread_id=thread_id, closed_by=closed_by)

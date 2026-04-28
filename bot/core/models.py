"""
Core domain models. No Discord objects, no DB objects — pure business logic types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class ThreadStatus(str, Enum):
    OPEN = "open"
    AWAITING_ANSWER = "awaiting_answer"
    AI_PROCESSING = "ai_processing"
    AWAITING_FOLLOWUP = "awaiting_followup"
    HUMAN_REVIEW = "human_review"
    CLOSED = "closed"


class AIMode(str, Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    HUMAN_OVERRIDE = "human_override"  # mod locked this thread to manual


@dataclass
class QAPair:
    question: str
    answer: str
    asked_at: datetime = field(default_factory=datetime.utcnow)
    answered_at: Optional[datetime] = None


@dataclass
class ThreadState:
    thread_id: str
    user_id: int
    guild_id: int
    channel_id: int                         # the private mod channel
    status: ThreadStatus
    ai_mode: AIMode
    question_round: int = 0
    qa_history: list[QAPair] = field(default_factory=list)
    ai_decision: Optional[str] = None       # APPROVE | VISITOR | REJECT
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # ── State machine guards ───────────────────────────────────────────────────

    @property
    def ai_active(self) -> bool:
        return self.ai_mode == AIMode.ENABLED

    @property
    def can_ask_followup(self, max_rounds: int = 3) -> bool:
        return self.question_round < max_rounds

    @property
    def is_awaiting_input(self) -> bool:
        return self.status in (
            ThreadStatus.AWAITING_ANSWER,
            ThreadStatus.AWAITING_FOLLOWUP,
        )

    @property
    def unanswered_questions(self) -> list[QAPair]:
        return [qa for qa in self.qa_history if qa.answered_at is None]

    # ── Transitions ───────────────────────────────────────────────────────────

    def record_answer(self, answer: str) -> None:
        for qa in self.qa_history:
            if qa.answered_at is None:
                qa.answer = answer
                qa.answered_at = datetime.utcnow()
                break
        self.updated_at = datetime.utcnow()

    def add_question(self, question: str) -> QAPair:
        qa = QAPair(question=question, answer="")
        self.qa_history.append(qa)
        self.updated_at = datetime.utcnow()
        return qa

    def set_ai_decision(
        self,
        decision: str,
        confidence: float,
        reasoning: str,
    ) -> None:
        self.ai_decision = decision
        self.ai_confidence = confidence
        self.ai_reasoning = reasoning
        self.updated_at = datetime.utcnow()

    def transition(self, new_status: ThreadStatus) -> None:
        self.status = new_status
        self.updated_at = datetime.utcnow()

    def format_qa_for_prompt(self) -> str:
        answered = [qa for qa in self.qa_history if qa.answered_at]
        return "\n\n".join(
            f"Q: {qa.question}\nA: {qa.answer}" for qa in answered
        )

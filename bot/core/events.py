"""
Domain events — typed dataclasses emitted by the gateway layer and consumed
by the core. Nothing Discord-specific lives here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BaseEvent:
    occurred_at: datetime = field(default_factory=datetime.utcnow)

    def to_json(self) -> str:
        def _default(obj: Any) -> str:
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        return json.dumps(asdict(self), default=_default)

    @classmethod
    def from_json(cls, data: str) -> "BaseEvent":
        return cls(**json.loads(data))


# ── Gateway events (Discord → Core) ───────────────────────────────────────────

@dataclass
class DMReceivedEvent(BaseEvent):
    user_id: int = 0
    content: str = ""
    attachments: list[str] = field(default_factory=list)


@dataclass
class ModReplyEvent(BaseEvent):
    thread_id: str = ""
    mod_id: int = 0
    content: str = ""
    is_internal_note: bool = False  # prefixed with //


@dataclass
class ThreadCloseRequestedEvent(BaseEvent):
    thread_id: str = ""
    closed_by: int = 0
    reason: str = ""


# ── Verification events ────────────────────────────────────────────────────────

@dataclass
class VerificationAnswerEvent(BaseEvent):
    thread_id: str = ""
    user_id: int = 0
    answer: str = ""
    question_index: int = 0


@dataclass
class AIJudgeRequestedEvent(BaseEvent):
    thread_id: str = ""
    trigger: str = "auto"   # auto | manual (mod triggered)


@dataclass
class AIDecisionReadyEvent(BaseEvent):
    thread_id: str = ""
    decision: str = ""      # APPROVE | VISITOR | REJECT
    confidence: float = 0.0
    reasoning: str = ""
    follow_up_required: bool = False
    auto_executable: bool = False


@dataclass
class HumanReviewRequiredEvent(BaseEvent):
    thread_id: str = ""
    reason: str = ""        # llm_failure | low_confidence | max_rounds_exceeded | schema_error


@dataclass
class ModOverrideEvent(BaseEvent):
    thread_id: str = ""
    mod_id: int = 0
    override_decision: str = ""   # APPROVE | VISITOR | REJECT
    original_ai_decision: str = ""

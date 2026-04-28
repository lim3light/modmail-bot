"""
Strict schemas for all AI inputs and outputs.
Nothing enters or exits the AI layer without passing through here.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AIDecision(BaseModel):
    """The structured output the LLM must return. Any deviation is a hard failure."""

    decision: Literal["APPROVE", "VISITOR", "REJECT"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=10, max_length=600)
    follow_up_required: bool = False

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 3)

    @field_validator("reasoning")
    @classmethod
    def strip_reasoning(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Reasoning cannot be empty")
        return cleaned

    def meets_threshold(self, thresholds: dict[str, float]) -> bool:
        """Check if this decision can be auto-executed based on configured thresholds."""
        return self.confidence >= thresholds.get(self.decision, 1.0)


class VerificationContext(BaseModel):
    """All the context the AI judge needs to evaluate a thread."""

    thread_id: str
    user_id: int
    guild_id: int
    question_round: int
    max_rounds: int
    server_context: str
    formatted_qa: str           # pre-formatted Q&A string from ThreadState

    @field_validator("formatted_qa")
    @classmethod
    def must_have_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Cannot evaluate with empty Q&A history")
        return v

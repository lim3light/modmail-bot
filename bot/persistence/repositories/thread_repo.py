"""
ThreadRepository — all database reads and writes for ThreadState.
Uses Redis as a write-through cache to keep hot threads in memory.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import asyncpg
import structlog

from bot.core.models import AIMode, QAPair, ThreadState, ThreadStatus
from bot.persistence.cache import CacheClient

log = structlog.get_logger(__name__)

CACHE_TTL = 3600   # 1 hour — evicted after a thread goes cold


class ThreadRepository:
    def __init__(self, pool: asyncpg.Pool, cache: CacheClient) -> None:
        self._pool = pool
        self._cache = cache

    # ── Read ───────────────────────────────────────────────────────────────────

    async def get(self, thread_id: str) -> Optional[ThreadState]:
        # 1. Try cache first
        cached = await self._cache.get(f"thread:{thread_id}")
        if cached:
            return self._deserialise(json.loads(cached))

        # 2. Fallback to DB
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM threads WHERE thread_id = $1", thread_id
            )
            if row is None:
                return None

            qa_rows = await conn.fetch(
                "SELECT question, answer, asked_at, answered_at "
                "FROM thread_qa WHERE thread_id = $1 ORDER BY id",
                thread_id,
            )

        state = self._from_row(row, qa_rows)
        await self._set_cache(state)
        return state

    async def get_by_channel(self, channel_id: int) -> Optional[ThreadState]:
        thread_id = await self._cache.get(f"channel:{channel_id}")
        if thread_id:
            return await self.get(thread_id)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT thread_id FROM channel_thread_map WHERE channel_id = $1",
                channel_id,
            )
        if row is None:
            return None

        tid = str(row["thread_id"])
        await self._cache.set(f"channel:{channel_id}", tid, ex=CACHE_TTL)
        return await self.get(tid)

    async def get_open_threads(self, guild_id: int) -> list[ThreadState]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT thread_id FROM threads WHERE guild_id = $1 AND status != 'closed'",
                guild_id,
            )
        results = []
        for row in rows:
            state = await self.get(str(row["thread_id"]))
            if state:
                results.append(state)
        return results

    # ── Write ──────────────────────────────────────────────────────────────────

    async def save(self, state: ThreadState) -> None:
        state.updated_at = datetime.utcnow()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO threads
                        (thread_id, user_id, guild_id, channel_id, status, ai_mode,
                         question_round, ai_decision, ai_confidence, ai_reasoning,
                         created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (thread_id) DO UPDATE SET
                        status         = EXCLUDED.status,
                        ai_mode        = EXCLUDED.ai_mode,
                        question_round = EXCLUDED.question_round,
                        ai_decision    = EXCLUDED.ai_decision,
                        ai_confidence  = EXCLUDED.ai_confidence,
                        ai_reasoning   = EXCLUDED.ai_reasoning,
                        updated_at     = EXCLUDED.updated_at
                    """,
                    state.thread_id, state.user_id, state.guild_id,
                    state.channel_id, state.status.value, state.ai_mode.value,
                    state.question_round, state.ai_decision,
                    state.ai_confidence, state.ai_reasoning,
                    state.created_at, state.updated_at,
                )

                # Upsert channel mapping
                await conn.execute(
                    """
                    INSERT INTO channel_thread_map (channel_id, thread_id)
                    VALUES ($1, $2)
                    ON CONFLICT (channel_id) DO NOTHING
                    """,
                    state.channel_id, state.thread_id,
                )

                # Sync Q&A — delete and reinsert is safe here (small rows)
                await conn.execute(
                    "DELETE FROM thread_qa WHERE thread_id = $1", state.thread_id
                )
                for qa in state.qa_history:
                    await conn.execute(
                        """
                        INSERT INTO thread_qa (thread_id, question, answer, asked_at, answered_at)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        state.thread_id, qa.question, qa.answer,
                        qa.asked_at, qa.answered_at,
                    )

        await self._set_cache(state)
        await self._cache.set(f"channel:{state.channel_id}", state.thread_id, ex=CACHE_TTL)

    # ── Serialisation helpers ──────────────────────────────────────────────────

    def _from_row(self, row: asyncpg.Record, qa_rows: list[asyncpg.Record]) -> ThreadState:
        qa = [
            QAPair(
                question=r["question"],
                answer=r["answer"],
                asked_at=r["asked_at"],
                answered_at=r["answered_at"],
            )
            for r in qa_rows
        ]
        return ThreadState(
            thread_id=str(row["thread_id"]),
            user_id=row["user_id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            status=ThreadStatus(row["status"]),
            ai_mode=AIMode(row["ai_mode"]),
            question_round=row["question_round"],
            qa_history=qa,
            ai_decision=row["ai_decision"],
            ai_confidence=float(row["ai_confidence"]) if row["ai_confidence"] else None,
            ai_reasoning=row["ai_reasoning"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _deserialise(self, data: dict) -> ThreadState:
        qa = [
            QAPair(
                question=q["question"],
                answer=q["answer"],
                asked_at=datetime.fromisoformat(q["asked_at"]),
                answered_at=datetime.fromisoformat(q["answered_at"]) if q["answered_at"] else None,
            )
            for q in data.get("qa_history", [])
        ]
        return ThreadState(
            thread_id=data["thread_id"],
            user_id=data["user_id"],
            guild_id=data["guild_id"],
            channel_id=data["channel_id"],
            status=ThreadStatus(data["status"]),
            ai_mode=AIMode(data["ai_mode"]),
            question_round=data["question_round"],
            qa_history=qa,
            ai_decision=data.get("ai_decision"),
            ai_confidence=data.get("ai_confidence"),
            ai_reasoning=data.get("ai_reasoning"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )

    async def _set_cache(self, state: ThreadState) -> None:
        payload = {
            "thread_id": state.thread_id,
            "user_id": state.user_id,
            "guild_id": state.guild_id,
            "channel_id": state.channel_id,
            "status": state.status.value,
            "ai_mode": state.ai_mode.value,
            "question_round": state.question_round,
            "qa_history": [
                {
                    "question": qa.question,
                    "answer": qa.answer,
                    "asked_at": qa.asked_at.isoformat(),
                    "answered_at": qa.answered_at.isoformat() if qa.answered_at else None,
                }
                for qa in state.qa_history
            ],
            "ai_decision": state.ai_decision,
            "ai_confidence": state.ai_confidence,
            "ai_reasoning": state.ai_reasoning,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        }
        await self._cache.set(f"thread:{state.thread_id}", json.dumps(payload), ex=CACHE_TTL)

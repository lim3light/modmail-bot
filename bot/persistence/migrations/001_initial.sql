-- Initial schema for the ModMail + AI verification system.
-- Run automatically by docker-compose on first boot via initdb.d.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Threads ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS threads (
    thread_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT NOT NULL,
    guild_id        BIGINT NOT NULL,
    channel_id      BIGINT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'open',
    ai_mode         TEXT NOT NULL DEFAULT 'disabled',
    question_round  INT NOT NULL DEFAULT 0,
    ai_decision     TEXT,
    ai_confidence   NUMERIC(5, 3),
    ai_reasoning    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_threads_user_id ON threads(user_id);
CREATE INDEX IF NOT EXISTS idx_threads_status  ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_channel ON threads(channel_id);

-- ── Q&A history ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS thread_qa (
    id          BIGSERIAL PRIMARY KEY,
    thread_id   UUID NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    asked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_qa_thread ON thread_qa(thread_id);

-- ── Messages (full modmail log) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS thread_messages (
    id              BIGSERIAL PRIMARY KEY,
    thread_id       UUID NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    author_id       BIGINT NOT NULL,
    content         TEXT NOT NULL,
    is_internal     BOOLEAN NOT NULL DEFAULT FALSE,
    discord_msg_id  BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON thread_messages(thread_id);

-- ── Audit log (append-only, never deleted) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    thread_id   UUID REFERENCES threads(thread_id),
    actor_type  TEXT NOT NULL,  -- 'ai' | 'mod' | 'system'
    actor_id    BIGINT,         -- mod user id, NULL for ai/system
    event_type  TEXT NOT NULL,  -- 'ai_decision' | 'mod_override' | 'thread_closed' etc.
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_thread ON audit_log(thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_event  ON audit_log(event_type);

-- ── Channel → thread mapping (cached in Redis, backed here) ───────────────────
CREATE TABLE IF NOT EXISTS channel_thread_map (
    channel_id  BIGINT PRIMARY KEY,
    thread_id   UUID NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE
);

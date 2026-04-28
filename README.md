# ModMail Bot with AI Verification

A production-grade Discord ModMail bot with an optional AI-powered verification pipeline.

## Quick start (local)

### 1. Prerequisites

- Python 3.11+
- Docker + docker-compose (for Postgres + Redis)
- A Discord bot token ([Discord developer portal](https://discord.com/developers/applications))

### 2. Clone and install

```bash
git clone <your-repo>
cd modmail-bot

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — fill in DISCORD_TOKEN, DISCORD_GUILD_ID, role IDs, channel IDs
```

Leave `LLM_PROVIDER=mock` while developing locally — no API keys needed.

### 4. Start infrastructure

```bash
docker-compose up -d
# Postgres + Redis start up; migrations run automatically
```

### 5. Run the bot

```bash
python main.py
```

---

## Discord bot setup

In the [Discord developer portal](https://discord.com/developers/applications):

1. **Bot → Privileged Gateway Intents**: enable `Message Content Intent` and `Server Members Intent`
2. **OAuth2 → URL Generator**: select `bot` + `applications.commands`, then select permissions:
   - Read Messages, Send Messages, Manage Channels, Manage Roles, Kick Members
3. Invite the bot to your server using the generated URL

---

## Project structure

```
modmail-bot/
├── main.py                          # Entry point
├── bot/
│   ├── config.py                    # Settings (pydantic-settings, reads .env)
│   ├── gateway/
│   │   ├── bot.py                   # Bot class, dependency wiring, cog loading
│   │   └── cogs/
│   │       ├── modmail.py           # DM handler, thread creation, message forwarding
│   │       └── moderation.py        # Slash commands (/modmail close|ai|override|status)
│   ├── core/
│   │   ├── models.py                # ThreadState, AIMode, ThreadStatus
│   │   ├── events.py                # Typed domain events
│   │   └── thread_manager.py        # Orchestrator — owns the full thread lifecycle
│   ├── ai/
│   │   ├── judge.py                 # AIJudgeService — retry, parse, validate
│   │   ├── schemas.py               # AIDecision, VerificationContext (Pydantic)
│   │   └── providers/
│   │       ├── base.py              # BaseLLMProvider interface
│   │       ├── anthropic_provider.py
│   │       ├── openai_provider.py
│   │       └── mock_provider.py     # Local dev, no API key needed
│   ├── actions/
│   │   └── executor.py              # ONLY component that writes to Discord API
│   └── persistence/
│       ├── database.py              # asyncpg pool
│       ├── cache.py                 # Redis wrapper
│       ├── migrations/
│       │   └── 001_initial.sql      # Full schema (auto-applied by docker-compose)
│       └── repositories/
│           └── thread_repo.py       # ThreadState reads/writes with Redis cache
└── tests/
    └── test_ai_judge.py
```

---

## Slash commands

| Command | Description |
|---|---|
| `/modmail close [reason]` | Close and archive the current thread |
| `/modmail ai enable\|disable` | Toggle AI judge for this thread |
| `/modmail override APPROVE\|VISITOR\|REJECT` | Manually override AI decision |
| `/modmail verify` | Manually trigger AI evaluation |
| `/modmail status` | Show current thread state |

All commands require **Manage Messages** permission.

---

## Switching LLM providers

Set `LLM_PROVIDER` in `.env`:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-20250514
```

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o
```

No code changes needed — the provider is injected at startup.

---

## Running tests

```bash
pytest tests/ -v
```

Tests use mock providers — no API keys or running infrastructure needed.

---

## Deployment (Heroku / DigitalOcean)

### Environment variables

Set all values from `.env.example` as platform environment variables.
Use managed Postgres and Redis add-ons — **do not** run docker-compose in production.

### Heroku

```bash
heroku create your-bot-name
heroku addons:create heroku-postgresql:mini
heroku addons:create heroku-redis:mini
heroku config:set DISCORD_TOKEN=... ENV=production
git push heroku main
heroku ps:scale web=1
```

Run migrations manually after first deploy:
```bash
heroku pg:psql < bot/persistence/migrations/001_initial.sql
```

### DigitalOcean App Platform

Use a **Worker** dyno type (not Web — the bot doesn't serve HTTP).
Point the run command to `python main.py`.

---

## AI verification flow

```
User DMs bot
    └─▶ Thread created in mod category
        └─▶ AI mode enabled?
            ├─ No  → Thread parked for manual mod review
            └─ Yes → Verification questions sent to user
                      └─▶ User answers
                           └─▶ LLM evaluates answers
                                ├─ confidence ≥ threshold → Action executed automatically
                                ├─ follow_up_required     → Follow-up question sent
                                ├─ confidence < threshold → Escalated to human review
                                └─ LLM failure            → Escalated to human review
```

Mods can override any AI decision at any time with `/modmail override`.

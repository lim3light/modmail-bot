# ModMail Bot Architecture & Code Walkthrough

This document provides an in-depth breakdown of the `modmail-bot` project, detailing the dependencies used, the overall architecture, and the codebase structure.

> [!NOTE]
> This bot functions as a production-grade Discord ModMail system that incorporates an optional AI-powered verification pipeline to automatically judge, accept, or reject users based on their answers to custom questions.

---

## 📦 Dependencies & Libraries Used

The project relies on several modern, asynchronous Python libraries to ensure high performance and strict typing.

### Core Libraries
- **`discord.py` (>=2.3.2)**: The primary library for interacting with the Discord API. Used for setting up the bot, receiving messages, interacting via slash commands, and performing moderation actions (roles, kicking, managing threads).
- **`pydantic` & `pydantic-settings`**: Used for strict data validation schema definitions (e.g., parsing the LLM's JSON outputs reliably into an `AIDecision` format) and loading configuration variables from `.env` files and environment variables.
- **`structlog`**: A robust, structured logging library. In production, logs are outputted in JSON format for easy parsing and monitoring, whereas in development, it provides pretty, colored console output.

### Infrastructure & Persistence
- **`asyncpg`**: A fast database interface library designed specifically for PostgreSQL and Python/asyncio. It is used to store persistent thread data.
- **`redis[asyncio]` & `arq`**: Redis and its asyncio integration are used for caching and managing queues (`arq` is commonly used for async job queues, though it may be utilized for background moderation tasks or caching in this project).

### AI & LLM Integration
- **`httpx`**: An asynchronous HTTP client used internally by the LLM providers to make fast, non-blocking requests to chosen AI models.
- **`anthropic` & `openai`**: Optional SDKs installed depending on the provider chosen. Used to interact with Claude (Anthropic) or GPT (OpenAI) models.
- **`python-dotenv`**: Used to load environment variables from the `.env` file during local development.

### Dev Dependencies
- **`pytest`, `pytest-asyncio`, `pytest-mock`**: The testing suite, relying on `asyncio` for testing asynchronous endpoints and mocking dependencies (like the mock LLM provider).
- **`ruff` & `mypy`**: Used for linting, formatting, and strict static type checking within the project ensuring fewer runtime errors.

---

## 🏗️ Codebase Architecture & Flow

The codebase strictly adheres to cleanly separated layers separating discord endpoints (gateway) from the business logic (core/ai) and data-saving mechanisms (persistence).

### 1. `main.py`
The project's entry point.
- **Role**: Configures structured logging via `structlog` depending on whether it's local (`ConsoleRenderer`) or production (`JSONRenderer`).
- Initializes `ModMailBot` and manages graceful startup and shutdown bindings.

### 2. `bot/config.py`
- Acts as the central configuration hub using Pydantic's `BaseSettings`.
- Exposes settings for the bot (Tokens, Guild IDs, Roles, Channels) and limits (AI confidence thresholds, Max LLM retries). Configurations are loaded safely and typed.

### 3. `bot/gateway/bot.py`
The connection to Discord and Dependency Injector.
- **`setup_hook`**: Crucial async setup section where all systems are initialized once the bot logs in. This creates the database pools, connects to Redis, injects the `ThreadRepository`, initializes the specific `BaseLLMProvider` (Anthropic, OpenAI, or Mock), initializes the `ThreadManager`, and loads in Discord Cogs (slash commands).

### 4. `bot/core/thread_manager.py`
The brain of the ModMail process. 
- **Role**: Manages the complete lifecycle of a user thread.
- Contains all major transition methods: `open_thread`, `start_verification`, `receive_answer`.
- **Logic**: It maintains state via `ThreadState` and sends default or follow-up verification questions. Once all questions are answered, it transitions the thread to `AI_PROCESSING` and initiates an evaluation request.
- Based on the outcome, it executes decisions (via `executor.py`) or escalates to human review.

### 5. `bot/ai/` (The AI Layer)
Handles the core logic of communicating with Large Language Models safely.
- **`judge.py` (`AIJudgeService`)**: Formats verification contexts into `SYSTEM_PROMPT` and `USER_PROMPT`. It calls the LLM, validates that the output perfectly matches the `AIDecision` JSON schema, and implements extensive retry mechanisms (`RETRY_DELAYS`) to gracefully bounce back from timeouts or poorly formatted LLM outputs.
- **`schemas.py`**: Pydantic definitions for safe AI output.
- **`providers/`**: Implementations for respective API connectors (`OpenAIProvider`, `AnthropicProvider`, etc.), adhering to the `BaseLLMProvider` contract.

### 6. `bot/actions/executor.py` 
- Serves as the *only* component allowed to modify user states directly against the Discord API (e.g., executing the "kick", "give_role_approved" actions decided by the human or AI).

### 7. `bot/persistence/`
Database integration.
- **`thread_repo.py` (`ThreadRepository`)**: Abstraction over `asyncpg` and `redis`. Fetches and persists the `ThreadState` data seamlessly without the domain logic needing to care about SQL.
- **`migrations/`**: Raw SQL files applied sequentially to scaffold the PostgreSQL database schema.

---

## 🤖 The Verification Workflow summarized

1. **User joins / DMs the bot** -> A new thread is tracked (`ThreadManager.open_thread`).
2. **AI starts interaction** -> Sends `DEFAULT_QUESTIONS` asynchronously. Thread is `AWAITING_ANSWER`.
3. **User Replies received** -> Iteratively answers. Once finished, thread moves to `AI_PROCESSING`.
4. **AI Evaluates** -> `AIJudgeService` requests an evaluation. Given constraints in `SYSTEM_PROMPT`, the LLM either accepts (`APPROVE`), limits (`VISITOR`), rejects (`REJECT`), or asks a `FOLLOW_UP`.
5. **Execution** -> If AI is highly confident (e.g., >80%), `executor.py` natively applies Discord roles/actions. If it isn't, the thread is escalated to mods (`HUMAN_REVIEW`). Mods can manually `/modmail override` at any time over discord.

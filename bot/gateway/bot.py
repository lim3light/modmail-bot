"""
Bot factory — creates the discord.py Bot instance, wires all dependencies,
and loads cogs. All dependency construction happens here, in one place.
"""
from __future__ import annotations

import discord
import structlog
from discord.ext import commands

from bot.actions.executor import ActionExecutor
from bot.ai.judge import AIJudgeService
from bot.config import Settings
from bot.core.thread_manager import ThreadManager
from bot.gateway.cogs.moderation import ModerationCog
from bot.gateway.cogs.modmail import ModmailCog
from bot.persistence.cache import CacheClient
from bot.persistence.database import create_pool
from bot.persistence.repositories.thread_repo import ThreadRepository

log = structlog.get_logger(__name__)


def _build_llm_provider(settings: Settings):
    """Factory — returns the correct LLM provider based on config."""
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        from bot.ai.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    elif provider == "openai":
        from bot.ai.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    elif provider == "mock":
        from bot.ai.providers.mock_provider import MockLLMProvider
        log.warning("using_mock_llm_provider — not suitable for production")
        return MockLLMProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Use 'anthropic', 'openai', or 'mock'.")


class ModMailBot(commands.Bot):
    """Extended Bot with settings attached and async setup hook."""

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.dm_messages = True

        super().__init__(
            command_prefix="!",   # unused — all commands are slash
            intents=intents,
            help_command=None,
        )
        self.settings = settings

    async def setup_hook(self) -> None:
        """Called once after login, before the bot is ready. Wire all dependencies here."""

        # ── Infrastructure ─────────────────────────────────────────────────────
        pool = await create_pool(self.settings.database_url)
        cache = CacheClient(self.settings.redis_url)

        # ── Repositories ───────────────────────────────────────────────────────
        thread_repo = ThreadRepository(pool=pool, cache=cache)

        # ── AI layer ───────────────────────────────────────────────────────────
        llm_provider = _build_llm_provider(self.settings)
        ai_judge = AIJudgeService(
            provider=llm_provider,
            server_context=self.settings.server_context,
            max_retries=self.settings.llm_max_retries,
        )

        # ── Action executor ────────────────────────────────────────────────────
        executor = ActionExecutor(bot=self, settings=self.settings)

        # ── Core ───────────────────────────────────────────────────────────────
        thread_manager = ThreadManager(
            settings=self.settings,
            thread_repo=thread_repo,
            ai_judge=ai_judge,
            action_executor=executor,
        )

        # ── Cogs ───────────────────────────────────────────────────────────────
        await self.add_cog(ModmailCog(bot=self, thread_manager=thread_manager))
        await self.add_cog(ModerationCog(bot=self, thread_manager=thread_manager))

        # Sync slash commands to the target guild only (instant, vs global which takes 1hr)
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("slash_commands_synced", count=len(synced))

    async def on_ready(self) -> None:
        log.info(
            "bot_ready",
            user=str(self.user),
            guild_count=len(self.guilds),
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="DMs | /modmail",
            )
        )

    async def on_error(self, event: str, *args, **kwargs) -> None:
        log.exception("unhandled_event_error", event=event)

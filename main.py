"""
Entry point. Configures structured logging, loads settings, starts the bot.
"""
import asyncio
import logging
import sys

import structlog

from bot.config import get_settings
from bot.gateway.bot import ModMailBot


def configure_logging(log_level: str) -> None:
    """Set up structlog with JSON output in production, pretty output locally."""
    settings = get_settings()

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_production:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Silence noisy discord.py internal logs in production
    logging.getLogger("discord").setLevel(
        logging.WARNING if settings.is_production else logging.INFO
    )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    log = structlog.get_logger(__name__)
    log.info("starting_bot", env=settings.env, llm_provider=settings.llm_provider)

    bot = ModMailBot(settings=settings)

    try:
        await bot.start(settings.discord_token)
    except KeyboardInterrupt:
        log.info("shutdown_requested")
    finally:
        if not bot.is_closed():
            await bot.close()
        log.info("bot_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

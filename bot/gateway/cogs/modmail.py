"""
Modmail cog — handles incoming DMs and routes them through the thread lifecycle.
This is the thinnest possible layer: receive Discord event → call core → done.
No business logic lives here.
"""
from __future__ import annotations

import discord
import structlog
from discord.ext import commands

from bot.core.thread_manager import ThreadManager

log = structlog.get_logger(__name__)


class ModmailCog(commands.Cog):
    def __init__(self, bot: commands.Bot, thread_manager: ThreadManager) -> None:
        self.bot = bot
        self.thread_manager = thread_manager

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # ── Incoming DM from user ──────────────────────────────────────────────
        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm(message)
            return

        # ── Message inside a modmail thread channel ────────────────────────────
        if isinstance(message.channel, discord.TextChannel):
            await self._handle_thread_message(message)

    # ── DM handler ─────────────────────────────────────────────────────────────

    async def _handle_dm(self, message: discord.Message) -> None:
        user = message.author
        guild = self._get_target_guild()
        if guild is None:
            return

        log.info("dm_received", user_id=user.id)

        # Check if a thread already exists for this user
        existing = await self._find_open_thread_for_user(user.id)

        if existing:
            # Forward message to existing thread channel
            channel = guild.get_channel(existing.channel_id)
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    description=message.content,
                    colour=discord.Colour.blurple(),
                )
                embed.set_author(name=str(user), icon_url=user.display_avatar.url)
                await channel.send(embed=embed)

            # If the thread is waiting for a verification answer, record it
            if existing.is_awaiting_input:
                await self.thread_manager.receive_answer(existing.thread_id, message.content)

            await message.add_reaction("✅")
            return

        # No existing thread — create a new one
        await self._open_new_thread(user, guild, message)

    async def _open_new_thread(
        self,
        user: discord.User,
        guild: discord.Guild,
        message: discord.Message,
    ) -> None:
        settings = self.bot.settings  # type: ignore[attr-defined]

        # Create the private channel in the modmail category
        category = guild.get_channel(settings.modmail_category_id)
        if not isinstance(category, discord.CategoryChannel):
            log.error("modmail_category_not_found", category_id=settings.modmail_category_id)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        # Give mod roles read access
        for role in guild.roles:
            if role.permissions.manage_messages:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )

        channel = await guild.create_text_channel(
            name=f"modmail-{user.name}-{user.discriminator}",
            category=category,
            overwrites=overwrites,
            reason=f"ModMail thread for {user} ({user.id})",
        )

        # Determine if AI mode should be on (global default from settings)
        ai_enabled = getattr(settings, "ai_mode_default", False)

        state = await self.thread_manager.open_thread(
            user_id=user.id,
            guild_id=guild.id,
            channel_id=channel.id,
            ai_mode_enabled=ai_enabled,
        )

        # Post the header embed in the mod channel
        header = discord.Embed(
            title=f"New ModMail — {user}",
            description=message.content or "*(no message)*",
            colour=discord.Colour.og_blurple(),
        )
        header.add_field(name="User ID", value=str(user.id), inline=True)
        header.add_field(name="Thread ID", value=state.thread_id[:8], inline=True)
        header.add_field(
            name="AI Judge",
            value="🟢 Enabled" if ai_enabled else "⚫ Disabled",
            inline=True,
        )
        header.set_thumbnail(url=user.display_avatar.url)
        await channel.send(embed=header)

        # Confirm to user
        await user.send(
            "👋 Thanks for reaching out! A moderator will review your message shortly. "
            "Please answer any questions we send you here."
        )
        await message.add_reaction("✅")

        # If AI is enabled, kick off verification immediately
        if ai_enabled:
            questions = await self.thread_manager.start_verification(state.thread_id)
            question_block = "\n\n".join(
                f"**{i+1}.** {qa.question}" for i, qa in enumerate(questions)
            )
            await user.send(
                f"Before we get started, please answer these quick questions:\n\n{question_block}"
            )

        log.info("thread_opened_via_dm", thread_id=state.thread_id, user_id=user.id)

    # ── Thread channel message handler ─────────────────────────────────────────

    async def _handle_thread_message(self, message: discord.Message) -> None:
        # Internal notes start with // — logged but not forwarded
        is_internal = message.content.startswith("//")

        state = await self.thread_manager.repo.get_by_channel(message.channel.id)
        if state is None:
            return  # Not a modmail channel

        if is_internal:
            await message.add_reaction("🔒")
            return

        # Forward mod reply to the user's DM
        user = self.bot.get_user(state.user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(state.user_id)
            except discord.NotFound:
                log.warning("user_not_found", user_id=state.user_id)
                return

        embed = discord.Embed(
            description=message.content,
            colour=discord.Colour.green(),
        )
        embed.set_author(
            name=f"Mod: {message.author.display_name}",
            icon_url=message.author.display_avatar.url,
        )
        try:
            await user.send(embed=embed)
            await message.add_reaction("✅")
        except discord.Forbidden:
            await message.reply("⚠️ Could not DM this user — they may have DMs disabled.")

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _get_target_guild(self) -> discord.Guild | None:
        settings = self.bot.settings  # type: ignore[attr-defined]
        return self.bot.get_guild(settings.discord_guild_id)

    async def _find_open_thread_for_user(self, user_id: int):
        guild = self._get_target_guild()
        if guild is None:
            return None
        open_threads = await self.thread_manager.repo.get_open_threads(guild.id)
        for t in open_threads:
            if t.user_id == user_id:
                return t
        return None

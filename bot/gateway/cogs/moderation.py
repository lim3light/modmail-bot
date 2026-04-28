"""
Moderation cog — slash commands for mod controls.
All commands are guild-scoped and require manage_messages permission.
"""
from __future__ import annotations

from typing import Literal

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from bot.core.thread_manager import ThreadManager

log = structlog.get_logger(__name__)

MOD_PERMISSION = app_commands.checks.has_permissions(manage_messages=True)


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, thread_manager: ThreadManager) -> None:
        self.bot = bot
        self.thread_manager = thread_manager

    modmail_group = app_commands.Group(
        name="modmail",
        description="ModMail management commands",
    )

    # ── /modmail close ─────────────────────────────────────────────────────────

    @modmail_group.command(name="close", description="Close the current modmail thread.")
    @MOD_PERMISSION
    async def close(self, interaction: discord.Interaction, reason: str = "No reason given") -> None:
        await interaction.response.defer(ephemeral=True)

        state = await self.thread_manager.repo.get_by_channel(interaction.channel_id)
        if state is None:
            await interaction.followup.send("❌ This is not a modmail thread.", ephemeral=True)
            return

        await self.thread_manager.close_thread(state.thread_id, interaction.user.id)

        # Archive the channel instead of deleting to preserve logs
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.edit(
                name=f"closed-{interaction.channel.name}",
                reason=f"Thread closed by {interaction.user} — {reason}",
            )

        await interaction.followup.send(f"✅ Thread closed. Reason: {reason}", ephemeral=True)
        log.info(
            "thread_closed_by_mod",
            thread_id=state.thread_id,
            mod_id=interaction.user.id,
            reason=reason,
        )

    # ── /modmail ai ────────────────────────────────────────────────────────────

    @modmail_group.command(name="ai", description="Toggle AI judge mode for this thread.")
    @MOD_PERMISSION
    async def toggle_ai(
        self,
        interaction: discord.Interaction,
        mode: Literal["enable", "disable"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        state = await self.thread_manager.repo.get_by_channel(interaction.channel_id)
        if state is None:
            await interaction.followup.send("❌ This is not a modmail thread.", ephemeral=True)
            return

        enabled = mode == "enable"
        await self.thread_manager.set_ai_mode(state.thread_id, enabled, interaction.user.id)

        status = "🟢 enabled" if enabled else "⚫ disabled"
        await interaction.followup.send(f"AI Judge mode is now **{status}** for this thread.", ephemeral=True)

    # ── /modmail override ──────────────────────────────────────────────────────

    @modmail_group.command(
        name="override",
        description="Manually override the AI decision for this thread.",
    )
    @MOD_PERMISSION
    async def override(
        self,
        interaction: discord.Interaction,
        decision: Literal["APPROVE", "VISITOR", "REJECT"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        state = await self.thread_manager.repo.get_by_channel(interaction.channel_id)
        if state is None:
            await interaction.followup.send("❌ This is not a modmail thread.", ephemeral=True)
            return

        await self.thread_manager.apply_mod_override(
            thread_id=state.thread_id,
            mod_id=interaction.user.id,
            decision=decision,
        )

        icons = {"APPROVE": "✅", "VISITOR": "👁️", "REJECT": "❌"}
        await interaction.followup.send(
            f"{icons[decision]} Override applied: **{decision}**. "
            f"Action executed and thread closed.",
            ephemeral=True,
        )
        log.info(
            "mod_override_command",
            thread_id=state.thread_id,
            mod_id=interaction.user.id,
            decision=decision,
        )

    # ── /modmail verify ────────────────────────────────────────────────────────

    @modmail_group.command(
        name="verify",
        description="Manually trigger AI verification for this thread.",
    )
    @MOD_PERMISSION
    async def verify(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        state = await self.thread_manager.repo.get_by_channel(interaction.channel_id)
        if state is None:
            await interaction.followup.send("❌ This is not a modmail thread.", ephemeral=True)
            return

        if not state.qa_history:
            await interaction.followup.send(
                "❌ No Q&A history yet. Start verification first with `/modmail start`.",
                ephemeral=True,
            )
            return

        from bot.core.models import ThreadStatus
        state_fresh = await self.thread_manager.repo.get(state.thread_id)
        if state_fresh:
            state_fresh.transition(ThreadStatus.AI_PROCESSING)
            await self.thread_manager.repo.save(state_fresh)
            # Kick off evaluation in background — don't block the interaction
            import asyncio
            asyncio.create_task(
                self.thread_manager._run_ai_evaluation(state_fresh)
            )

        await interaction.followup.send(
            "🤖 AI evaluation triggered. Results will appear in the mod log.",
            ephemeral=True,
        )

    # ── /modmail status ────────────────────────────────────────────────────────

    @modmail_group.command(name="status", description="Show the current thread state.")
    @MOD_PERMISSION
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        state = await self.thread_manager.repo.get_by_channel(interaction.channel_id)
        if state is None:
            await interaction.followup.send("❌ This is not a modmail thread.", ephemeral=True)
            return

        embed = discord.Embed(title="Thread Status", colour=discord.Colour.blurple())
        embed.add_field(name="Thread ID", value=state.thread_id[:8], inline=True)
        embed.add_field(name="Status", value=state.status.value, inline=True)
        embed.add_field(name="AI Mode", value=state.ai_mode.value, inline=True)
        embed.add_field(name="User", value=f"<@{state.user_id}>", inline=True)
        embed.add_field(name="Q Rounds", value=str(state.question_round), inline=True)

        if state.ai_decision:
            embed.add_field(
                name="AI Decision",
                value=f"{state.ai_decision} ({state.ai_confidence:.0%})",
                inline=True,
            )
            embed.add_field(name="AI Reasoning", value=state.ai_reasoning or "—", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Error handler ──────────────────────────────────────────────────────────

    @modmail_group.error
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Manage Messages** permission to use this command.",
                ephemeral=True,
            )
        else:
            log.error("slash_command_error", error=str(error))
            await interaction.response.send_message(
                "❌ An unexpected error occurred.", ephemeral=True
            )

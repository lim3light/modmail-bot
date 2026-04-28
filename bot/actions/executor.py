"""
ActionExecutor — the only component that executes write actions on Discord.
Nothing else in the system is allowed to assign roles, kick, or ban.
"""
from __future__ import annotations

import discord
import structlog

from bot.ai.schemas import AIDecision
from bot.config import Settings
from bot.core.models import ThreadState

log = structlog.get_logger(__name__)


class ActionExecutor:
    def __init__(self, bot: discord.Client, settings: Settings) -> None:
        self._bot = bot
        self._settings = settings

    async def execute(self, state: ThreadState, decision: AIDecision) -> None:
        """
        Map an AI decision to Discord actions.
        Validates everything before touching the Discord API.
        """
        guild = self._bot.get_guild(state.guild_id)
        if guild is None:
            log.error("guild_not_found", guild_id=state.guild_id, thread_id=state.thread_id)
            return

        try:
            member = await guild.fetch_member(state.user_id)
        except discord.NotFound:
            log.warning(
                "member_left_during_processing",
                user_id=state.user_id,
                thread_id=state.thread_id,
            )
            return

        action = decision.decision
        log.info(
            "executing_action",
            thread_id=state.thread_id,
            user_id=state.user_id,
            action=action,
            confidence=decision.confidence,
        )

        if action == "APPROVE":
            await self._approve(guild, member, state, decision)
        elif action == "VISITOR":
            await self._visitor(guild, member, state, decision)
        elif action == "REJECT":
            await self._reject(guild, member, state, decision)
        else:
            log.error("unknown_action", action=action, thread_id=state.thread_id)

        await self._log_to_mod_channel(guild, state, decision)

    # ── Action implementations ─────────────────────────────────────────────────

    async def _approve(
        self,
        guild: discord.Guild,
        member: discord.Member,
        state: ThreadState,
        decision: AIDecision,
    ) -> None:
        await self._swap_role(
            guild, member,
            remove_role_id=self._settings.role_unverified,
            add_role_id=self._settings.role_approved,
            reason=f"AI verification: APPROVE (confidence {decision.confidence:.0%})",
        )
        await self._dm_user(
            member,
            "✅ **Verification approved!** You now have full access to the server. "
            "Welcome — we're glad to have you.",
        )

    async def _visitor(
        self,
        guild: discord.Guild,
        member: discord.Member,
        state: ThreadState,
        decision: AIDecision,
    ) -> None:
        await self._swap_role(
            guild, member,
            remove_role_id=self._settings.role_unverified,
            add_role_id=self._settings.role_visitor,
            reason=f"AI verification: VISITOR (confidence {decision.confidence:.0%})",
        )
        await self._dm_user(
            member,
            "👁️ **Limited access granted.** You've been given visitor status. "
            "A moderator may review your application further.",
        )

    async def _reject(
        self,
        guild: discord.Guild,
        member: discord.Member,
        state: ThreadState,
        decision: AIDecision,
    ) -> None:
        await self._dm_user(
            member,
            "❌ **Your verification was unsuccessful.** "
            "You've been removed from the server. If you believe this is an error, "
            "you may appeal via [your appeal link].",
        )
        try:
            await guild.kick(
                member,
                reason=f"AI verification: REJECT (confidence {decision.confidence:.0%})",
            )
        except discord.Forbidden:
            log.error(
                "kick_forbidden",
                user_id=member.id,
                thread_id=state.thread_id,
            )

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _swap_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        remove_role_id: int,
        add_role_id: int,
        reason: str,
    ) -> None:
        remove_role = guild.get_role(remove_role_id)
        add_role = guild.get_role(add_role_id)

        if add_role is None:
            log.error("role_not_found", role_id=add_role_id)
            return

        try:
            if remove_role and remove_role in member.roles:
                await member.remove_roles(remove_role, reason=reason)
            await member.add_roles(add_role, reason=reason)
        except discord.Forbidden:
            log.error("role_assignment_forbidden", user_id=member.id, role_id=add_role_id)
        except discord.HTTPException as e:
            log.error("role_assignment_failed", user_id=member.id, error=str(e))

    async def _dm_user(self, member: discord.Member, content: str) -> None:
        try:
            await member.send(content)
        except discord.Forbidden:
            log.warning("dm_blocked_by_user", user_id=member.id)

    async def _log_to_mod_channel(
        self,
        guild: discord.Guild,
        state: ThreadState,
        decision: AIDecision,
    ) -> None:
        channel = guild.get_channel(self._settings.mod_log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        colour_map = {
            "APPROVE": discord.Colour.green(),
            "VISITOR": discord.Colour.orange(),
            "REJECT": discord.Colour.red(),
        }

        embed = discord.Embed(
            title=f"AI Verification — {decision.decision}",
            colour=colour_map.get(decision.decision, discord.Colour.greyple()),
        )
        embed.add_field(name="User", value=f"<@{state.user_id}>", inline=True)
        embed.add_field(name="Confidence", value=f"{decision.confidence:.0%}", inline=True)
        embed.add_field(name="Reasoning", value=decision.reasoning, inline=False)
        embed.set_footer(text=f"Thread {state.thread_id} · Override with /modmail override")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.error("mod_log_failed", error=str(e))

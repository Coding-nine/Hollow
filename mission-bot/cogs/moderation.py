"""Moderation: roles, kick/ban/mute, clear, warnings and the word-filter automod."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands

from utils.embeds import success_embed
from utils.errors import UserError

log = logging.getLogger(__name__)

BANNED_WORDS_FILE = Path(__file__).resolve().parent.parent / "banned_words.txt"
MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord's limit


# ----------------------------------------------------------------- word filter
def parse_banned_words(text: str) -> list[str]:
    """Read the banned-words file format (comments with #, one word per line)."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def compile_banned_words(words: list[str]) -> re.Pattern[str] | None:
    """Build one regex matching any of the words as whole words.

    A trailing ``*`` also allows longer forms (``fuck*`` -> fucking, fucker...).
    """
    parts = []
    for word in sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True):
        if word.endswith("*"):
            parts.append(re.escape(word[:-1]) + r"\w*")
        else:
            parts.append(re.escape(word))
    if not parts:
        return None
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE)


def find_banned_word(pattern: re.Pattern[str] | None, text: str) -> str | None:
    if pattern is None:
        return None
    match = pattern.search(text)
    return match.group(0).lower() if match else None


class Moderation(commands.Cog):
    """Keep your server tidy and safe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.banned_pattern: re.Pattern[str] | None = None
        self.banned_count = 0

    async def cog_load(self) -> None:
        try:
            words = parse_banned_words(BANNED_WORDS_FILE.read_text(encoding="utf-8"))
        except OSError:
            log.warning("%s not found - automod has no words to filter", BANNED_WORDS_FILE)
            words = []
        self.banned_pattern = compile_banned_words(words)
        self.banned_count = len(words)

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage
        return True

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _check_can_act_on(ctx: commands.Context, target: discord.Member) -> None:
        """Raise a friendly error unless both you and the bot outrank ``target``."""
        if target == ctx.author:
            raise UserError("You can't do that to yourself.")
        if target == ctx.guild.me:
            raise UserError("Nice try.")
        if target == ctx.guild.owner:
            raise UserError("You can't do that to the server owner.")
        if ctx.author != ctx.guild.owner and ctx.author.top_role <= target.top_role:
            raise UserError("Your highest role isn't above theirs.")
        if ctx.guild.me.top_role <= target.top_role:
            raise UserError("My highest role isn't above theirs - move my role higher in Server Settings > Roles.")

    @staticmethod
    def _audit(ctx: commands.Context, reason: str | None) -> str:
        """Audit-log reason text (Discord caps it at 512 characters)."""
        return f"{ctx.author}: {reason or 'no reason given'}"[:512]

    @staticmethod
    def _check_can_manage_role(ctx: commands.Context, role: discord.Role) -> None:
        if role.is_default() or role.managed:
            raise UserError("That role can't be given or removed manually.")
        if ctx.author != ctx.guild.owner and ctx.author.top_role <= role:
            raise UserError("You are not strong enough to do that - that role isn't below yours.")
        if ctx.guild.me.top_role <= role:
            raise UserError("That role is above mine - move my role higher in Server Settings > Roles.")

    def _add_warning(self, user_id: int, reason: str) -> int:
        record = self.bot.db.reports.ensure(user_id)
        number = max((w["warn_number"] for w in record["warns"]), default=0) + 1
        record["warns"].append(
            {
                "warn_number": number,
                "reason": reason,
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self.bot.db.reports.save()
        return number

    # --------------------------------------------------------------- roles
    @commands.command(aliases=["give_role"])
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def giverole(self, ctx: commands.Context, role: discord.Role, member: discord.Member) -> None:
        """Give a role to a member."""
        self._check_can_manage_role(ctx, role)
        await member.add_roles(role, reason=f"Given by {ctx.author}")
        await ctx.send(embed=success_embed(f"{member.mention} has been given the role **{role.name}**."))

    @commands.command(aliases=["remove_role"])
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(self, ctx: commands.Context, role: discord.Role, member: discord.Member) -> None:
        """Take a role away from a member."""
        self._check_can_manage_role(ctx, role)
        await member.remove_roles(role, reason=f"Removed by {ctx.author}")
        await ctx.send(embed=success_embed(f"The role **{role.name}** has been taken from {member.mention}."))

    # ------------------------------------------------------- kick / ban / mute
    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        """Kick a member."""
        self._check_can_act_on(ctx, member)
        await member.kick(reason=self._audit(ctx, reason))
        await ctx.send(embed=success_embed(f"Kicked **{member}**. Reason: {reason or 'none given'}"))

    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        """Ban a member."""
        self._check_can_act_on(ctx, member)
        await member.ban(reason=self._audit(ctx, reason))
        await ctx.send(embed=success_embed(f"Banned **{member}**. Reason: {reason or 'none given'}"))

    @commands.command(aliases=["timeout"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(
        self, ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str | None = None
    ) -> None:
        """Mute (time out) a member for some minutes (default 10, max 28 days)."""
        self._check_can_act_on(ctx, member)
        if not 1 <= minutes <= MAX_TIMEOUT_MINUTES:
            raise UserError(f"Minutes must be between 1 and {MAX_TIMEOUT_MINUTES:,}.")
        await member.timeout(timedelta(minutes=minutes), reason=self._audit(ctx, reason))
        await ctx.send(embed=success_embed(f"Muted {member.mention} for **{minutes}** minute(s)."))

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove a member's mute (time out)."""
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        await ctx.send(embed=success_embed(f"Unmuted {member.mention}."))

    @commands.command(aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear(self, ctx: commands.Context, amount: int = 10) -> None:
        """Delete the last few messages in this channel (1-100)."""
        if not 1 <= amount <= 100:
            raise UserError("Pick a number between 1 and 100.")
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 = your command message
        await ctx.send(embed=success_embed(f"Deleted {max(len(deleted) - 1, 0)} message(s)."), delete_after=5)

    # --------------------------------------------------------------- warnings
    @commands.command()
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str) -> None:
        """Warn a member. A reason is required."""
        if len(reason) > 500:
            raise UserError("Keep the reason under 500 characters.")
        number = self._add_warning(member.id, reason)
        embed = discord.Embed(title="Moderation", colour=discord.Colour.red())
        embed.add_field(name=f"Warned (#{number})", value=f"Successfully warned {member.mention}. Reason: {reason}")
        await ctx.send(embed=embed)

    @commands.command()
    async def warns(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show a member's warnings (defaults to you)."""
        member = member or ctx.author
        record = self.bot.db.reports.get(member.id)
        warns = record["warns"] if record else []
        embed = discord.Embed(title=f"{member.display_name}'s warnings", colour=discord.Colour.orange())
        if not warns:
            embed.description = "No warnings. 🎉"
        for warn in warns[-10:]:
            embed.add_field(
                name=f"#{warn['warn_number']} - {warn.get('time', 'unknown time')}", value=warn["reason"], inline=False
            )
        if len(warns) > 10:
            embed.set_footer(text=f"Showing the latest 10 of {len(warns)}")
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(kick_members=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove all of a member's warnings."""
        record = self.bot.db.reports.ensure(member.id)
        record["warns"] = []
        self.bot.db.reports.save()
        await ctx.send(embed=success_embed(f"Cleared all warnings for {member.mention}."))

    # ----------------------------------------------------------------- automod
    @commands.group(invoke_without_command=True)
    async def automod(self, ctx: commands.Context) -> None:
        """Show what automod is and whether it's on in this server."""
        enabled = self.bot.db.servers.ensure(ctx.guild.id)["automod"]
        embed = discord.Embed(
            title="Auto Moderation",
            description="**Auto Moderation** deletes messages containing banned words and warns the author.",
            colour=discord.Colour.orange(),
        )
        embed.add_field(name="State", value="**enabled**" if enabled else "**disabled**")
        embed.add_field(name="Banned words", value=f"{self.banned_count} (kept private)")
        p = ctx.clean_prefix
        embed.set_footer(text=f"Admins: {p}automod enable / {p}automod disable")
        await ctx.send(embed=embed)

    @automod.command(name="enable", aliases=["on"])
    @commands.has_permissions(administrator=True)
    async def automod_enable(self, ctx: commands.Context) -> None:
        """Turn automod on."""
        await self._toggle_automod(ctx, True)

    @automod.command(name="disable", aliases=["off"])
    @commands.has_permissions(administrator=True)
    async def automod_disable(self, ctx: commands.Context) -> None:
        """Turn automod off."""
        await self._toggle_automod(ctx, False)

    @automod.command(name="state")
    async def automod_state(self, ctx: commands.Context) -> None:
        """Show whether automod is on."""
        enabled = self.bot.db.servers.ensure(ctx.guild.id)["automod"]
        await ctx.send(f"Automod is currently **{'enabled' if enabled else 'disabled'}**.")

    async def _toggle_automod(self, ctx: commands.Context, enable: bool) -> None:
        settings = self.bot.db.servers.ensure(ctx.guild.id)
        if settings["automod"] == enable:
            raise UserError(f"Automod is already {'enabled' if enable else 'disabled'}.")
        settings["automod"] = enable
        self.bot.db.servers.save()
        await ctx.send(embed=success_embed(f"Automod has been **{'enabled' if enable else 'disabled'}**."))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        settings = self.bot.db.servers.get(message.guild.id)
        if not settings or not settings["automod"]:
            return
        word = find_banned_word(self.banned_pattern, message.content)
        if word is None:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            return  # missing permission or already deleted - don't warn for something we couldn't remove

        reason = f"Using a blacklisted word --Blacklisted Word : ||{word}||"
        number = self._add_warning(message.author.id, reason)
        embed = discord.Embed(title="Moderation", colour=discord.Colour.red())
        embed.add_field(
            name=f"Warned (#{number})", value=f"Successfully warned {message.author.mention}. Reason: {reason}"
        )
        try:
            await message.channel.send(embed=embed, delete_after=10)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))

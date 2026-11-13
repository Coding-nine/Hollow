"""Per-server settings (admin only): prefix, welcome/leave channels, counting channel."""

from __future__ import annotations

import discord
from discord.ext import commands

from utils.embeds import success_embed
from utils.errors import UserError


class Settings(commands.Cog):
    """Admin commands for configuring the bot in your server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        # Applies to every command in this cog, including subcommands of `set`.
        if ctx.guild is None:
            raise commands.NoPrivateMessage
        if not ctx.author.guild_permissions.administrator:
            raise commands.MissingPermissions(["administrator"])
        return True

    # ---------------------------------------------------------------- commands
    @commands.group(name="set", invoke_without_command=True)
    async def set_(self, ctx: commands.Context) -> None:
        """Show this server's settings. Subcommands change them."""
        guild_id = ctx.guild.id
        settings = self.bot.db.servers.ensure(guild_id)
        counting = self.bot.db.counting.ensure(guild_id)

        def channel(channel_id: int | None) -> str:
            return f"<#{channel_id}>" if channel_id else "not set"

        embed = discord.Embed(title="Server settings", colour=discord.Colour.blurple())
        embed.add_field(name="Prefix", value=f"`{settings['prefix']}`")
        embed.add_field(name="Welcome channel", value=channel(settings["welcome_channel"]))
        embed.add_field(name="Leave channel", value=channel(settings["leave_channel"]))
        embed.add_field(name="Counting channel", value=channel(counting["counting_channel"]))
        embed.add_field(name="Automod", value="on" if settings["automod"] else "off")
        embed.set_footer(text=f"Change one with {ctx.clean_prefix}set <prefix|welcome|leave|counting> ...")
        await ctx.send(embed=embed)

    @set_.command(name="prefix")
    async def set_prefix(self, ctx: commands.Context, prefix: str) -> None:
        """Change the command prefix for this server (max 5 characters)."""
        if len(prefix) > 5:
            raise UserError("The prefix can be at most 5 characters long.")
        settings = self.bot.db.servers.ensure(ctx.guild.id)
        old, settings["prefix"] = settings["prefix"], prefix
        self.bot.db.servers.save()
        await ctx.send(embed=success_embed(f"Changed prefix from `{old}` to `{prefix}`."))

    @set_.command(name="welcome")
    async def set_welcome(self, ctx: commands.Context, channel: discord.TextChannel | str) -> None:
        """Set the channel for welcome messages (or `off` to disable them)."""
        await self._set_channel(ctx, "welcome_channel", channel, "Welcome")

    @set_.command(name="leave")
    async def set_leave(self, ctx: commands.Context, channel: discord.TextChannel | str) -> None:
        """Set the channel for leave messages (or `off` to disable them)."""
        await self._set_channel(ctx, "leave_channel", channel, "Leave")

    @set_.command(name="counting")
    async def set_counting(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the counting-game channel (this restarts the count at 1)."""
        state = self.bot.db.counting.ensure(ctx.guild.id)
        state.update(counting_channel=channel.id, count=1, old_author=None)
        self.bot.db.counting.save()
        await ctx.send(embed=success_embed(f"Counting channel set to {channel.mention}."))
        try:
            await channel.send("This is now the counting channel - please start with `1`!")
        except discord.Forbidden:
            raise UserError(f"Saved, but I can't send messages in {channel.mention}.") from None

    async def _set_channel(
        self, ctx: commands.Context, key: str, channel: discord.TextChannel | str, label: str
    ) -> None:
        if not isinstance(channel, discord.TextChannel) and channel.lower() != "off":
            raise UserError("Mention a channel (like #general), or type `off` to turn it off.")
        settings = self.bot.db.servers.ensure(ctx.guild.id)
        if isinstance(channel, discord.TextChannel):
            settings[key] = channel.id
            message = f"{label} channel set to {channel.mention}."
        else:
            settings[key] = None
            message = f"{label} messages turned off."
        self.bot.db.servers.save()
        await ctx.send(embed=success_embed(message))

    # ------------------------------------------------------------------ events
    async def _announce(self, member: discord.Member, key: str, text: str) -> None:
        record = self.bot.db.servers.get(member.guild.id)
        if record is None or not record[key]:
            return
        channel = member.guild.get_channel(record[key])
        if isinstance(channel, discord.TextChannel) and channel.permissions_for(member.guild.me).send_messages:
            await channel.send(text)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._announce(member, "welcome_channel", f"Welcome to **{member.guild.name}**, {member.mention}! 🎉")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self._announce(member, "leave_channel", f"**{member.display_name}** has left the server. 👋")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))

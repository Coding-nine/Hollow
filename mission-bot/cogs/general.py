"""Small everyday commands: ping, info, serverinfo, botinfo."""

from __future__ import annotations

import discord
from discord.ext import commands

from utils.embeds import requested_by


class General(commands.Cog):
    """Everyday utility commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    async def ping(self, ctx: commands.Context) -> None:
        """Show the bot's latency."""
        embed = discord.Embed(
            title="Ping",
            description=f":ping_pong: Pong! Latency: **`{round(self.bot.latency * 1000)}ms`**",
            colour=ctx.author.colour,
        )
        await ctx.send(embed=requested_by(embed, ctx))

    @commands.command()
    async def botinfo(self, ctx: commands.Context) -> None:
        """About this bot."""
        try:
            owner = (await self.bot.application_info()).owner
            creator = f" My creator is **{owner}**."
        except discord.HTTPException:
            creator = ""
        prefix = ctx.clean_prefix
        await ctx.send(
            "I am Mission Bot, originally made for the sole purpose of serving the "
            f"Mission-Mountain servers.{creator} For more use **`{prefix}help`**"
        )

    @commands.command(aliases=["userinfo", "whois"])
    @commands.guild_only()
    async def info(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show info about a member (defaults to you)."""
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        embed = discord.Embed(title=str(member), colour=member.colour)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Nickname", value=member.nick or "None")
        embed.add_field(name="Bot", value="Yes" if member.bot else "No")
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "D"))
        if member.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "D"))
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=" ".join(roles[:10]) + (" ..." if len(roles) > 10 else "") if roles else "None",
            inline=False,
        )
        await ctx.send(embed=requested_by(embed, ctx))

    @commands.command(aliases=["server", "guildinfo"])
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Show info about this server."""
        guild = ctx.guild
        embed = discord.Embed(title=guild.name, description=guild.description, colour=discord.Colour.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
        embed.add_field(name="Members", value=f"{guild.member_count:,}")
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "D"))
        embed.add_field(name="Text channels", value=len(guild.text_channels))
        embed.add_field(name="Voice channels", value=len(guild.voice_channels))
        embed.add_field(name="Roles", value=len(guild.roles))
        embed.add_field(name="Boosts", value=f"{guild.premium_subscription_count} (tier {guild.premium_tier})")
        embed.set_footer(text=f"ID: {guild.id}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))

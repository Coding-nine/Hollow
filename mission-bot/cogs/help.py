"""A help command that builds itself from the cogs/commands that are loaded.

Add a command with a docstring and it shows up here automatically - there's no
separate list to keep in sync.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from utils.embeds import error_embed, requested_by

# Short names people already know from the old bot -> the cog's real name.
CATEGORY_ALIASES = {
    "mod": "moderation",
    "econ": "economy",
    "fun": "games",
    "admin": "settings",
    "misc": "general",
    "count": "counting",
    "level": "levels",
}

CATEGORY_EMOJI = {
    "Economy": "💰",
    "Games": "🎲",
    "Moderation": "🛡️",
    "Settings": "⚙️",
    "General": "🧭",
    "Counting": "🔢",
    "Levels": "⭐",
}


def _usage(prefix: str, command: commands.Command) -> str:
    return f"{prefix}{command.qualified_name} {command.signature}".strip()


class Help(commands.Cog):
    """Shows what the bot can do."""

    hidden = True

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _visible_cogs(self) -> list[commands.Cog]:
        return [
            cog
            for cog in self.bot.cogs.values()
            if not getattr(cog, "hidden", False) and any(not c.hidden for c in cog.get_commands())
        ]

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context, *, topic: str | None = None) -> None:
        """Show all commands, or details about a category or one command.

        Examples: `help`, `help economy`, `help buy`, `help set prefix`
        """
        if topic is None:
            await ctx.send(embed=self._overview(ctx))
            return

        key = topic.strip().lower()
        key = CATEGORY_ALIASES.get(key, key)

        for cog in self._visible_cogs():
            if cog.qualified_name.lower() == key:
                await ctx.send(embed=self._category(ctx, cog))
                return

        command = self.bot.get_command(key)
        if command is not None and not command.hidden:
            await ctx.send(embed=self._command(ctx, command))
            return

        await ctx.send(
            embed=error_embed(f"I don't know `{discord.utils.escape_markdown(topic)}`. Try `{ctx.clean_prefix}help`.")
        )

    # ---------------------------------------------------------------- embeds
    def _overview(self, ctx: commands.Context) -> discord.Embed:
        p = ctx.clean_prefix
        embed = discord.Embed(
            title="Help",
            description=f"Use **`{p}help <category>`** or **`{p}help <command>`** for more detail.",
            colour=discord.Colour.dark_red(),
        )
        for cog in self._visible_cogs():
            names = " ".join(f"`{c.name}`" for c in cog.get_commands() if not c.hidden)
            emoji = CATEGORY_EMOJI.get(cog.qualified_name, "📁")
            embed.add_field(name=f"{emoji} {cog.qualified_name}", value=names, inline=False)
        return requested_by(embed, ctx)

    def _category(self, ctx: commands.Context, cog: commands.Cog) -> discord.Embed:
        p = ctx.clean_prefix
        emoji = CATEGORY_EMOJI.get(cog.qualified_name, "📁")
        embed = discord.Embed(
            title=f"{emoji} {cog.qualified_name}",
            description=cog.description or None,
            colour=discord.Colour.blurple(),
        )
        for command in cog.get_commands():
            if command.hidden:
                continue
            embed.add_field(name=f"`{_usage(p, command)}`", value=command.short_doc or "No description.", inline=False)
        return requested_by(embed, ctx)

    def _command(self, ctx: commands.Context, command: commands.Command) -> discord.Embed:
        p = ctx.clean_prefix
        usage = _usage(p, command)
        embed = discord.Embed(
            title=f"{p}{command.qualified_name}",
            description=command.help or "No description.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
        if command.aliases:
            embed.add_field(name="Aliases", value=" ".join(f"`{a}`" for a in command.aliases), inline=False)
        if isinstance(command, commands.Group):
            subs = "\n".join(f"`{_usage(p, sub)}` - {sub.short_doc}" for sub in command.commands if not sub.hidden)
            if subs:
                embed.add_field(name="Subcommands", value=subs, inline=False)
        cooldown = command.cooldown  # None when the command has no cooldown
        if cooldown is not None:
            embed.add_field(name="Cooldown", value=f"{cooldown.rate} use(s) per {int(cooldown.per)}s", inline=False)
        return requested_by(embed, ctx)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))

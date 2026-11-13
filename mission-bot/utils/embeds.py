"""Small helpers so embeds look consistent across the bot."""

from __future__ import annotations

import discord
from discord.ext import commands


def error_embed(text: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {text}", colour=discord.Colour.red())


def success_embed(text: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {text}", colour=discord.Colour.green())


def requested_by(embed: discord.Embed, ctx: commands.Context) -> discord.Embed:
    embed.set_footer(
        text=f"Requested by {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url,
    )
    return embed


def humanize_seconds(seconds: float) -> str:
    """``3725`` -> ``'1h 2m 5s'`` (used for cooldown messages)."""
    seconds = max(int(seconds), 1)
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{unit}")
    return " ".join(parts[:3])

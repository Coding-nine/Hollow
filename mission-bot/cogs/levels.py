"""XP and levels, stored in MongoDB. Optional: only loads if MONGO_URI is set.

The connection string is read from the environment (see .env.example) and is
never written in code. Documents keep the original bot's shape - database
``discord``, collection ``levelling``, documents ``{"id": <user id>, "xp": <int>}``
- so XP you already have in your cluster keeps working.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import time
from collections.abc import Iterator

import discord
from discord.ext import commands
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import PyMongoError

from utils.embeds import requested_by
from utils.errors import UserError

log = logging.getLogger(__name__)

XP_PER_MESSAGE = 10
XP_COOLDOWN_SECONDS = 30  # a user only earns XP once per this many seconds
BAR_LENGTH = 20


def level_for(xp: int) -> int:
    """Level 1 at 50 XP, 2 at 200, 3 at 450, 10 at 5,000 ..."""
    return int(math.sqrt(max(xp, 0) / 50))


def xp_for_level(level: int) -> int:
    """Total XP needed to *reach* ``level``."""
    return 50 * level * level


def progress_bar(xp: int) -> tuple[str, int, int]:
    """Return ``(bar, xp_into_level, xp_needed_for_next)``."""
    level = level_for(xp)
    start, end = xp_for_level(level), xp_for_level(level + 1)
    into, needed = xp - start, end - start
    filled = int(into / needed * BAR_LENGTH)
    return "🟦" * filled + "⬜" * (BAR_LENGTH - filled), into, needed


class Levels(commands.Cog):
    """Earn XP by chatting and climb the ranks."""

    def __init__(self, bot: commands.Bot, uri: str) -> None:
        self.bot = bot
        self.client: AsyncMongoClient = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
        db_name = os.getenv("MONGO_DB", "discord")
        collection_name = os.getenv("MONGO_COLLECTION", "levelling")
        self.collection = self.client[db_name][collection_name]
        self._last_xp: dict[int, float] = {}

    async def cog_unload(self) -> None:
        await self.client.close()

    @staticmethod
    @contextlib.contextmanager
    def _db_errors() -> Iterator[None]:
        """Turn database outages into a friendly message instead of a traceback."""
        try:
            yield
        except PyMongoError as exc:
            log.error("MongoDB error: %s", exc)
            raise UserError("The level database isn't reachable right now. Try again later.") from exc

    # ------------------------------------------------------------------ XP
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        now = time.monotonic()
        if now - self._last_xp.get(message.author.id, -XP_COOLDOWN_SECONDS) < XP_COOLDOWN_SECONDS:
            return
        self._last_xp[message.author.id] = now

        try:
            doc = await self.collection.find_one_and_update(
                {"id": message.author.id},
                {"$inc": {"xp": XP_PER_MESSAGE}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            log.warning("Could not save XP: %s", exc)
            return

        xp = doc["xp"]
        if level_for(xp) > level_for(xp - XP_PER_MESSAGE):
            try:
                await message.channel.send(
                    f"Well done {message.author.mention}! You levelled up to **level {level_for(xp)}**! 🎉"
                )
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------- commands
    @commands.command(aliases=["level"])
    async def rank(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show your level, XP and rank (or someone else's)."""
        member = member or ctx.author
        with self._db_errors():
            doc = await self.collection.find_one({"id": member.id})
            if doc is None:
                raise UserError(f"{member.display_name} hasn't sent any messages yet.")
            xp = doc["xp"]
            position = await self.collection.count_documents({"xp": {"$gt": xp}}) + 1
            total = await self.collection.estimated_document_count()
        bar, into, needed = progress_bar(xp)

        embed = discord.Embed(title=f"{member.display_name}'s level stats", colour=member.colour)
        embed.add_field(name="Level", value=level_for(xp))
        embed.add_field(name="XP", value=f"{into:,}/{needed:,}")
        embed.add_field(name="Rank", value=f"{position}/{total}")
        embed.add_field(name="Progress", value=bar, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=requested_by(embed, ctx))

    @commands.command(aliases=["xpboard", "toplevels"])
    async def levels(self, ctx: commands.Context) -> None:
        """Show the top 10 by XP."""
        with self._db_errors():
            docs = await self.collection.find({}).sort("xp", -1).limit(10).to_list(10)
        lines = [
            f"`{i}.` <@{doc['id']}> - level **{level_for(doc['xp'])}** ({doc['xp']:,} XP)"
            for i, doc in enumerate(docs, start=1)
        ]
        embed = discord.Embed(
            title="Level leaderboard",
            description="\n".join(lines) or "Nobody has earned XP yet.",
            colour=discord.Colour.gold(),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    uri = os.getenv("MONGO_URI")
    if not uri:
        log.info("MONGO_URI is not set - the level system is disabled.")
        return
    await bot.add_cog(Levels(bot, uri))

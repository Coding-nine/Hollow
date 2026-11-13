"""The counting game, plus the server-token shop that sells 'saves' for it."""

from __future__ import annotations

import random
import re

import discord
from discord.ext import commands

from utils.embeds import requested_by, success_embed
from utils.errors import CommandBlocked, UserError

SAVE_PRICE = 1_000  # server tokens
NUMBER_RE = re.compile(r"[0-9]+")  # ASCII digits only


def process_count(state: dict, author_id: int, content: str) -> str:
    """Apply one message to a guild's counting state (mutates ``state``).

    Returns one of:
      ``"ignore"``  - not a number, so it's just chat
      ``"ok"``      - correct, the count moved on
      ``"saved"``   - a mistake, but a save absorbed it (count unchanged)
      ``"ruined"``  - a mistake with no saves left; the count was reset to 1
    """
    text = content.strip()
    if not NUMBER_RE.fullmatch(text):
        return "ignore"

    same_person = state["old_author"] == author_id
    if not same_person and int(text) == state["count"]:
        state["count"] += 1
        state["old_author"] = author_id
        return "ok"

    if state["saves"] > 0:
        state["saves"] -= 1
        return "saved"

    state["count"] = 1
    state["old_author"] = None
    return "ruined"


class Counting(commands.Cog):
    """Count together in the counting channel, and spend server tokens on saves."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_check(self._block_commands_in_counting_channel)

    async def cog_unload(self) -> None:
        self.bot.remove_check(self._block_commands_in_counting_channel)

    async def _block_commands_in_counting_channel(self, ctx: commands.Context) -> bool:
        """Global check: keep the counting channel free of bot commands."""
        if ctx.guild is None:
            return True
        state = self.bot.db.counting.get(ctx.guild.id)
        if state and state["counting_channel"] == ctx.channel.id:
            raise CommandBlocked("Commands can't be used in the counting channel.")
        return True

    # ------------------------------------------------------------- the game
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # Every message has a 50% chance to earn the server 3 tokens.
        if random.random() < 0.5:
            self.bot.db.servers.ensure(message.guild.id)["server_tokens"] += 3
            self.bot.db.servers.mark_dirty()

        state = self.bot.db.counting.get(message.guild.id)
        if not state or state["counting_channel"] != message.channel.id:
            return

        result = process_count(state, message.author.id, message.content)
        if result == "ignore":
            return

        score = self.bot.db.count_scores.ensure(message.author.id)
        if result == "ok":
            score["correct"] += 1
            score["score"] += 1
        else:
            score["wrong"] += 1
        self.bot.db.counting.mark_dirty()
        self.bot.db.count_scores.mark_dirty()

        try:
            if result == "ok":
                await message.add_reaction("✅")
            elif result == "saved":
                await message.add_reaction("⚠️")
                await message.channel.send(
                    f"{message.author.mention} made a mistake, but a save covered it! "
                    f"Saves left: **{state['saves']}**. The next number is **{state['count']}**."
                )
            else:
                await message.add_reaction("❌")
                await message.channel.send(f"{message.author.mention} ruined the counting! Try again - start with `1`.")
        except discord.HTTPException:
            pass  # missing permissions / message deleted - the state is already updated

    # -------------------------------------------------------------- commands
    @commands.command()
    @commands.guild_only()
    async def counting(self, ctx: commands.Context) -> None:
        """Show the state of this server's counting game."""
        state = self.bot.db.counting.ensure(ctx.guild.id)
        if not state["counting_channel"]:
            raise UserError(f"No counting channel yet. An admin can use `{ctx.clean_prefix}set counting #channel`.")
        embed = discord.Embed(title="Counting", colour=discord.Colour.blurple())
        embed.add_field(name="Channel", value=f"<#{state['counting_channel']}>")
        embed.add_field(name="Next number", value=state["count"])
        embed.add_field(name="Saves", value=state["saves"])
        await ctx.send(embed=embed)

    @commands.command(aliases=["cscore"])
    async def countstats(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show someone's counting score (defaults to you)."""
        member = member or ctx.author
        score = self.bot.db.count_scores.ensure(member.id)
        embed = discord.Embed(title=f"{member.display_name}'s counting stats", colour=discord.Colour.blurple())
        embed.add_field(name="Score", value=score["score"])
        embed.add_field(name="Correct", value=score["correct"])
        embed.add_field(name="Wrong", value=score["wrong"])
        await ctx.send(embed=embed)

    @commands.command(aliases=["countlb"])
    async def countboard(self, ctx: commands.Context) -> None:
        """Show the top 10 counters."""
        ranked = sorted(self.bot.db.count_scores.data.items(), key=lambda kv: kv[1]["score"], reverse=True)[:10]
        lines = [
            f"`{i}.` <@{uid}> - **{s['score']}** (✅ {s['correct']} / ❌ {s['wrong']})"
            for i, (uid, s) in enumerate(ranked, 1)
        ]
        embed = discord.Embed(
            title="Counting leaderboard",
            description="\n".join(lines) or "Nobody has counted yet.",
            colour=discord.Colour.blurple(),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------ server tokens/shop
    @commands.command()
    @commands.guild_only()
    async def serverbal(self, ctx: commands.Context) -> None:
        """Show how many server tokens this server has."""
        tokens = self.bot.db.servers.ensure(ctx.guild.id)["server_tokens"]
        embed = discord.Embed(title=f"{ctx.guild.name}'s Server Balance", colour=discord.Colour.green())
        embed.add_field(name="Server Tokens 🪙", value=f"Balance - {tokens:,}")
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.send(embed=requested_by(embed, ctx))

    @commands.command()
    async def servershop(self, ctx: commands.Context) -> None:
        """See what a server can buy with server tokens."""
        embed = discord.Embed(title="Server Shop", colour=discord.Colour.orange())
        embed.add_field(
            name="Saves 💾",
            value=f"{SAVE_PRICE:,} Server Tokens | Covers a counting mistake instead of resetting the count",
            inline=False,
        )
        embed.set_footer(text=f"Admins can buy with {ctx.clean_prefix}sbuy saves [amount]")
        await ctx.send(embed=embed)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def sbuy(self, ctx: commands.Context, item: str, amount: int = 1) -> None:
        """Buy counting saves with server tokens (admins only)."""
        if item.lower() not in ("save", "saves"):
            raise UserError(f"I only sell `saves` right now. See `{ctx.clean_prefix}servershop`.")
        if amount < 1:
            raise UserError("The amount must be at least 1.")

        settings = self.bot.db.servers.ensure(ctx.guild.id)
        cost = SAVE_PRICE * amount
        if settings["server_tokens"] < cost:
            have = settings["server_tokens"]
            raise UserError(
                f"You can't afford {amount} save(s): they cost **{cost:,}** and the server has **{have:,}**."
            )

        settings["server_tokens"] -= cost
        self.bot.db.counting.ensure(ctx.guild.id)["saves"] += amount
        self.bot.db.servers.save()
        self.bot.db.counting.save()
        await ctx.send(embed=success_embed(f"You bought **{amount}** save(s) for **{cost:,}** server tokens."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counting(bot))

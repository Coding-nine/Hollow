"""Drive the real cogs with fake Discord objects (no network, no token needed)."""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import os
import signal
import sys
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest
from discord.ext import commands

import bot as botmod
from cogs.games import CoinFlipView, HighLowView, TicTacToeView
from utils.errors import CommandBlocked, UserError
from utils.health import start_health_server
from utils.store import Database


# ------------------------------------------------------------------ helpers
def run(coro):
    return asyncio.run(coro)


async def make_bot(tmp_path, monkeypatch, *, mongo=True):
    if mongo:
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:1")  # lazy client: never connects
    else:
        monkeypatch.delenv("MONGO_URI", raising=False)
    db = Database(tmp_path)
    db.load()
    bot = botmod.MissionBot(db)
    bot._connection.user = MagicMock(id=999)
    await bot._async_setup_hook()  # what discord.py does just before setup_hook() on a real login
    await bot.setup_hook()
    return bot


def member(uid, name="user"):
    m = MagicMock(spec=discord.Member)
    m.id, m.display_name, m.bot = uid, name, False
    m.mention = f"<@{uid}>"
    m.colour = discord.Colour.blue()
    m.display_avatar.url = "https://example.com/a.png"
    return m


def ctx_for(author, guild=None):
    ctx = MagicMock()
    ctx.author, ctx.guild, ctx.clean_prefix = author, guild, "$"
    ctx.send = AsyncMock()
    return ctx


def sent_embed(ctx) -> discord.Embed:
    return ctx.send.call_args.kwargs["embed"]


def call(cog, name, ctx, *args, **kwargs):
    """Invoke a command's callback directly (skips cooldowns and permission checks)."""
    command = cog.bot.get_command(name)
    return command.callback(cog, ctx, *args, **kwargs)


def interaction_for(user):
    i = MagicMock()
    i.user = user
    i.response.edit_message = AsyncMock()
    i.response.send_message = AsyncMock()
    return i


# ------------------------------------------------------------------- loading
def test_every_cog_loads_and_levels_is_optional(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path / "a", monkeypatch, mongo=True)
        assert {"Core", "Help", "General", "Settings", "Moderation", "Economy", "Games", "Counting", "Levels"} <= set(
            bot.cogs
        )
        await bot.close()

        bot = await make_bot(tmp_path / "b", monkeypatch, mongo=False)
        assert "Levels" not in bot.cogs and "Economy" in bot.cogs
        await bot.close()

    run(scenario())


def test_prefix_resolution(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        bot.db.servers.ensure(5)["prefix"] = "?"
        in_guild = MagicMock(guild=MagicMock(id=5))
        in_dm = MagicMock(guild=None)
        unknown = MagicMock(guild=MagicMock(id=77))
        assert "?" in await bot.get_prefix(in_guild)
        assert "$" in await bot.get_prefix(in_dm)
        assert "$" in await bot.get_prefix(unknown)
        assert "<@999> " in await bot.get_prefix(in_guild)  # mention works as a prefix
        assert bot.db.servers.get(77) is None  # looking up a prefix creates nothing
        await bot.close()

    run(scenario())


# ------------------------------------------------------------------- economy
def test_economy_flow(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        eco = bot.get_cog("Economy")
        alice, bob = member(1, "alice"), member(2, "bob")
        ctx = ctx_for(alice)
        acc = bot.db.users.ensure(1)

        # can't afford a rolex, can afford a laptop
        with pytest.raises(UserError):
            await call(eco, "buy", ctx, "rolexwatch", 1)
        with pytest.raises(UserError):
            await call(eco, "buy", ctx, "banana", 1)
        await call(eco, "buy", ctx, "Laptop", 1)
        assert acc["wallet"] == 0 and acc["inventory"] == [{"item": "laptop", "amount": 1}]

        # sell it back for half
        await call(eco, "sell", ctx, "laptop", "1")
        assert acc["wallet"] == 500 and acc["inventory"] == []
        with pytest.raises(UserError):
            await call(eco, "sell", ctx, "laptop", "1")

        # bank
        await call(eco, "deposit", ctx, "300")
        assert (acc["wallet"], acc["bank"]) == (200, 300)
        await call(eco, "withdraw", ctx, "all")
        assert (acc["wallet"], acc["bank"]) == (500, 0)
        acc["bankcap"] = 100
        await call(eco, "deposit", ctx, "all")  # only 100 fits
        assert acc["bank"] == 100
        with pytest.raises(UserError):
            await call(eco, "deposit", ctx, "1")  # bank full
        with pytest.raises(UserError):
            await call(eco, "withdraw", ctx, "999")

        # send
        await call(eco, "send", ctx, bob, "150")
        assert acc["wallet"] == 250 and bot.db.users.get(2)["wallet"] == 1150
        with pytest.raises(UserError):
            await call(eco, "send", ctx, alice, "1")  # yourself
        with pytest.raises(UserError):
            await call(eco, "send", ctx, bob, "99999")

        # daily: once per 24h
        await call(eco, "daily", ctx)
        assert acc["wallet"] == 250 + 1000
        with pytest.raises(UserError):
            await call(eco, "daily", ctx)

        await call(eco, "balance", ctx)
        await call(eco, "leaderboard", ctx, 3)
        assert "<@2>" in sent_embed(ctx).description
        await call(eco, "profile", ctx)
        await call(eco, "shop", ctx)
        await call(eco, "inventory", ctx)
        await bot.close()

    run(scenario())


def test_earning_commands_need_their_tools(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        eco = bot.get_cog("Economy")
        ctx = ctx_for(member(1))
        acc = bot.db.users.ensure(1)
        acc["wallet"] = 20_000

        with pytest.raises(UserError):
            await call(eco, "postmeme", ctx)
        with pytest.raises(UserError):
            await call(eco, "fish", ctx)

        await call(eco, "buy", ctx, "laptop")
        await call(eco, "buy", ctx, "fishingrod")
        before = acc["wallet"]
        monkeypatch.setattr("cogs.economy.random.randint", lambda a, b: 1)  # never "nothing"/"bad meme"
        await call(eco, "postmeme", ctx)
        await call(eco, "fish", ctx)
        assert acc["wallet"] >= before
        assert any(e["item"].endswith("fish") for e in acc["inventory"])
        await call(eco, "sell", ctx, "commonfish", "all") if any(
            e["item"] == "commonfish" for e in acc["inventory"]
        ) else None

        await call(eco, "search", ctx)
        await call(eco, "beg", ctx)
        await bot.close()

    run(scenario())


def test_use_items(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        eco = bot.get_cog("Economy")
        ctx = ctx_for(member(1))
        acc = bot.db.users.ensure(1)
        acc["wallet"] = 50_000
        await call(eco, "buy", ctx, "bankcoin", 2)
        await call(eco, "buy", ctx, "multiplier", 1)
        await call(eco, "buy", ctx, "lotteryticket", 5)
        wallet_after_shopping = acc["wallet"]

        cap = acc["bankcap"]
        await call(eco, "use", ctx, "bankcoin", 2)
        assert acc["bankcap"] >= cap + 8000
        await call(eco, "use", ctx, "multiplier", 1)
        assert acc["multiplier"] == 2

        monkeypatch.setattr("cogs.economy.random.random", lambda: 0.99)  # every ticket loses
        await call(eco, "use", ctx, "lotteryticket", 5)
        assert acc["wallet"] == wallet_after_shopping - 50
        assert acc["inventory"] == []

        with pytest.raises(UserError):
            await call(eco, "use", ctx, "bankcoin", 1)  # none left
        with pytest.raises(UserError):
            await call(eco, "use", ctx, "laptop", 1)
        await bot.close()

    run(scenario())


# --------------------------------------------------------------------- games
def test_tictactoe_win_transfers_tokens(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        p1, p2 = member(1, "x-player"), member(2, "o-player")
        bot.db.users.ensure(1)["wallet"] = 100
        bot.db.users.ensure(2)["wallet"] = 5000
        view = TicTacToeView(bot, (p1, p2))
        view.turn = 0

        # a stranger can't play, and neither can the player whose turn it isn't
        assert not await view.interaction_check(interaction_for(member(3)))
        assert not await view.interaction_check(interaction_for(p2))
        assert await view.interaction_check(interaction_for(p1))

        for index in (0, 3, 1, 4, 2):  # X: 0,1,2 wins
            i = interaction_for(view.current)
            await view.play(i, view.children[index])
        assert view.is_finished()
        assert bot.db.users.get(1)["wallet"] == 1600  # +1500
        assert bot.db.users.get(2)["wallet"] == 3500  # -1500
        assert all(child.disabled for child in view.children)
        await bot.close()

    run(scenario())


def test_tictactoe_wager_capped_at_loser_wallet_and_ties(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        p1, p2 = member(1), member(2)
        bot.db.users.ensure(1)["wallet"] = 0
        bot.db.users.ensure(2)["wallet"] = 400
        view = TicTacToeView(bot, (p1, p2))
        view.turn = 0
        for index in (0, 3, 1, 4, 2):
            await view.play(interaction_for(view.current), view.children[index])
        assert bot.db.users.get(2)["wallet"] == 0 and bot.db.users.get(1)["wallet"] == 400  # no negative balances

        tie = TicTacToeView(bot, (p1, p2))
        tie.turn = 0
        last = None
        for index in (0, 1, 2, 4, 3, 5, 7, 6, 8):  # X O X / X O O / O X X -> draw
            last = interaction_for(tie.current)
            await tie.play(last, tie.children[index])
        assert "tie" in last.response.edit_message.call_args.kwargs["content"].lower()
        await bot.close()

    run(scenario())


def test_ttt_command_rejects_bots_and_self(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        games = bot.get_cog("Games")
        me = member(1)
        robot = member(2)
        robot.bot = True
        ctx = ctx_for(me, guild=MagicMock())
        with pytest.raises(UserError):
            await call(games, "ttt", ctx, robot)
        with pytest.raises(UserError):
            await call(games, "ttt", ctx, me)
        await call(games, "ttt", ctx, member(3))
        # (compared by name: unloading a cog evicts its module, so reloads create fresh classes)
        assert type(ctx.send.call_args.kwargs["view"]).__name__ == "TicTacToeView"
        await bot.close()

    run(scenario())


def test_highlow_and_coinflip(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        me = member(1)

        view = HighLowView(me, number=80, hint=50)
        assert not await view.interaction_check(interaction_for(member(2)))
        i = interaction_for(me)
        await view._answer(i, said_higher=True)
        assert "Correct" in i.response.edit_message.call_args.kwargs["content"]

        view = HighLowView(me, number=20, hint=50)
        i = interaction_for(me)
        await view._answer(i, said_higher=True)
        assert "Incorrect" in i.response.edit_message.call_args.kwargs["content"]

        acc = bot.db.users.ensure(1)
        acc["wallet"] = 1000
        monkeypatch.setattr("cogs.games.random.choice", lambda seq: "heads")
        monkeypatch.setattr("cogs.games.random.random", lambda: 0.99)  # no bank-cap bonus
        flip = CoinFlipView(bot, me, 400)
        await flip._flip(interaction_for(me), "heads")
        assert acc["wallet"] == 1400
        flip = CoinFlipView(bot, me, 400)
        await flip._flip(interaction_for(me), "tails")
        assert acc["wallet"] == 1000

        flip = CoinFlipView(bot, me, 5000)  # spent the money while the buttons were showing
        i = interaction_for(me)
        await flip._flip(i, "heads")
        assert acc["wallet"] == 1000 and "no longer" in i.response.edit_message.call_args.kwargs["content"]

        # the command itself validates the bet
        games = bot.get_cog("Games")
        ctx = ctx_for(me)
        with pytest.raises(UserError):
            await call(games, "cf", ctx, "999999")
        await call(games, "cf", ctx, "max")
        assert type(ctx.send.call_args.kwargs["view"]).__name__ == "CoinFlipView"
        assert ctx.send.call_args.kwargs["view"].bet == 1000
        await bot.close()

    run(scenario())


# ------------------------------------------------------------------ counting
def fake_message(author_id, guild_id, channel_id, content):
    msg = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.mention = f"<@{author_id}>"
    msg.guild.id = guild_id
    msg.channel.id = channel_id
    msg.channel.send = AsyncMock()
    msg.content = content
    msg.add_reaction = AsyncMock()
    return msg


def test_counting_listener_and_command_block(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        cog = bot.get_cog("Counting")
        bot.db.counting.ensure(10).update(counting_channel=55)

        m1 = fake_message(1, 10, 55, "1")
        await cog.on_message(m1)
        m1.add_reaction.assert_awaited_with("✅")
        assert bot.db.counting.get(10)["count"] == 2
        assert bot.db.count_scores.get(1)["score"] == 1

        chat = fake_message(2, 10, 55, "lol nice")
        await cog.on_message(chat)
        chat.add_reaction.assert_not_awaited()

        other_channel = fake_message(2, 10, 56, "2")  # a number elsewhere is just chat
        await cog.on_message(other_channel)
        assert bot.db.counting.get(10)["count"] == 2

        bad = fake_message(3, 10, 55, "9")
        await cog.on_message(bad)
        bad.add_reaction.assert_awaited_with("❌")
        bad.channel.send.assert_awaited()
        assert bot.db.counting.get(10)["count"] == 1
        assert bot.db.count_scores.get(3)["wrong"] == 1

        # commands are blocked in the counting channel only
        ctx = MagicMock()
        ctx.guild.id, ctx.channel.id = 10, 55
        with pytest.raises(CommandBlocked):
            await cog._block_commands_in_counting_channel(ctx)
        ctx.channel.id = 56
        assert await cog._block_commands_in_counting_channel(ctx)

        # bots and DMs are ignored
        bot_msg = fake_message(4, 10, 55, "1")
        bot_msg.author.bot = True
        await cog.on_message(bot_msg)
        dm = fake_message(4, 10, 55, "1")
        dm.guild = None
        await cog.on_message(dm)
        await bot.close()

    run(scenario())


def test_server_tokens_and_saves(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        cog = bot.get_cog("Counting")
        guild = MagicMock()
        guild.id = 10
        ctx = ctx_for(member(1), guild=guild)

        with pytest.raises(UserError):
            await call(cog, "sbuy", ctx, "saves", 1)  # server has 0 tokens
        with pytest.raises(UserError):
            await call(cog, "sbuy", ctx, "pizza", 1)

        bot.db.servers.ensure(10)["server_tokens"] = 2500
        await call(cog, "sbuy", ctx, "saves", 2)
        assert bot.db.servers.get(10)["server_tokens"] == 500
        assert bot.db.counting.get(10)["saves"] == 2
        with pytest.raises(UserError):
            await call(cog, "sbuy", ctx, "saves", 1)  # only 500 left: the old code had this check backwards

        await call(cog, "serverbal", ctx)
        await call(cog, "servershop", ctx)
        await call(cog, "countboard", ctx)
        await call(cog, "countstats", ctx)
        await bot.close()

    run(scenario())


# ---------------------------------------------------------------- moderation
@functools.total_ordering
class FakeRole:
    def __init__(self, position):
        self.position = position

    def __eq__(self, other):
        return self.position == other.position

    def __lt__(self, other):
        return self.position < other.position


def test_role_hierarchy_checks(tmp_path, monkeypatch):
    async def scenario():
        from cogs.moderation import Moderation

        owner = member(100)
        guild = MagicMock()
        guild.owner = owner
        guild.me.top_role = FakeRole(5)
        author = member(1)
        author.top_role = FakeRole(8)
        ctx = ctx_for(author, guild=guild)

        def target(position, uid=2):
            t = member(uid)
            t.top_role = FakeRole(position)
            return t

        Moderation._check_can_act_on(ctx, target(3))  # fine: author 8 > 3 and bot 5 > 3
        for bad in (author, guild.me, owner, target(8), target(9), target(5)):
            with pytest.raises(UserError):
                Moderation._check_can_act_on(ctx, bad)
        await asyncio.sleep(0)

    run(scenario())


def test_warn_numbering_and_automod(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        mod = bot.get_cog("Moderation")
        ctx = ctx_for(member(1), guild=MagicMock())
        target = member(7, "target")

        await call(mod, "warn", ctx, target, reason="first")
        await call(mod, "warn", ctx, target, reason="second")
        warns = bot.db.reports.get(7)["warns"]
        assert [w["warn_number"] for w in warns] == [1, 2]
        assert [w["reason"] for w in warns] == ["first", "second"]  # the old code always stored "Swearing"
        with pytest.raises(UserError):
            await call(mod, "warn", ctx, target, reason="x" * 501)

        await call(mod, "warns", ctx, target)
        assert len(sent_embed(ctx).fields) == 2
        await call(mod, "clearwarns", ctx, target)
        assert bot.db.reports.get(7)["warns"] == []

        # automod
        def msg(text, guild_id=10):
            m = fake_message(8, guild_id, 1, text)
            m.delete = AsyncMock()
            return m

        clean, rude, other_guild = msg("hello there"), msg("you fucking idiot"), msg("you fucker", guild_id=11)
        bot.db.servers.ensure(10)["automod"] = True
        for m in (clean, rude, other_guild):
            await mod.on_message(m)
        clean.delete.assert_not_awaited()
        rude.delete.assert_awaited()
        other_guild.delete.assert_not_awaited()  # automod is per-server
        assert bot.db.reports.get(8)["warns"][0]["reason"].startswith("Using a blacklisted word")

        # toggling
        ctx2 = ctx_for(member(1), guild=MagicMock(id=10))
        with pytest.raises(UserError):
            await mod._toggle_automod(ctx2, True)  # already on
        await mod._toggle_automod(ctx2, False)
        assert bot.db.servers.get(10)["automod"] is False
        await bot.close()

    run(scenario())


def test_mute_validates_minutes(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        mod = bot.get_cog("Moderation")
        author = member(1)
        author.top_role = FakeRole(8)
        guild = MagicMock()
        guild.owner = member(100)
        guild.me.top_role = FakeRole(5)
        ctx = ctx_for(author, guild=guild)
        target = member(2)
        target.top_role = FakeRole(1)
        target.timeout = AsyncMock()
        with pytest.raises(UserError):
            await call(mod, "mute", ctx, target, 0)
        with pytest.raises(UserError):
            await call(mod, "mute", ctx, target, 60 * 24 * 29)
        await call(mod, "mute", ctx, target, 15, reason="spam")
        target.timeout.assert_awaited_once()
        await bot.close()

    run(scenario())


# ------------------------------------------------------------------ settings
def test_settings_commands(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        cog = bot.get_cog("Settings")
        guild = MagicMock()
        guild.id = 10
        ctx = ctx_for(member(1), guild=guild)

        await call(cog, "set prefix", ctx, "!")
        assert bot.db.servers.get(10)["prefix"] == "!"
        with pytest.raises(UserError):
            await call(cog, "set prefix", ctx, "waytoolong")

        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.mention, channel.send = 55, "<#55>", AsyncMock()
        await call(cog, "set welcome", ctx, channel)
        assert bot.db.servers.get(10)["welcome_channel"] == 55
        await call(cog, "set welcome", ctx, "Off")
        assert bot.db.servers.get(10)["welcome_channel"] is None
        with pytest.raises(UserError):
            await call(cog, "set welcome", ctx, "banana")

        await call(cog, "set counting", ctx, channel)
        assert bot.db.counting.get(10)["counting_channel"] == 55
        channel.send.assert_awaited()
        await call(cog, "set", ctx)  # the settings overview

        # welcome / leave announcements
        await call(cog, "set leave", ctx, channel)
        guild.get_channel.return_value = channel
        channel.permissions_for.return_value.send_messages = True
        leaving = member(3, "bye")
        leaving.guild = guild
        await cog.on_member_remove(leaving)
        assert "bye" in channel.send.call_args.args[0]
        await bot.close()

    run(scenario())


# ----------------------------------------------------------- help + errors
def test_help_command(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        helper = bot.get_cog("Help")
        ctx = ctx_for(member(1))

        await call(helper, "help", ctx)
        names = [f.name for f in sent_embed(ctx).fields]
        assert any("Economy" in n for n in names) and any("Moderation" in n for n in names)
        assert not any("Core" in n or "Help" in n for n in names)  # internal cogs hidden
        assert "load" not in " ".join(f.value for f in sent_embed(ctx).fields)  # owner commands hidden

        for topic in ("econ", "mod", "fun", "admin", "misc", "Economy", "buy", "set prefix", "automod", "lb"):
            ctx = ctx_for(member(1))
            await call(helper, "help", ctx, topic=topic)
            assert "I don't know" not in (sent_embed(ctx).description or ""), topic

        ctx = ctx_for(member(1))
        await call(helper, "help", ctx, topic="nonsense")
        assert "don't know" in sent_embed(ctx).description
        ctx = ctx_for(member(1))
        await call(helper, "help", ctx, topic="load")  # hidden owner command
        assert "don't know" in sent_embed(ctx).description
        await bot.close()

    run(scenario())


def test_every_command_has_a_description(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        missing = [c.qualified_name for c in bot.walk_commands() if not c.hidden and not c.help]
        assert missing == []
        await bot.close()

    run(scenario())


def test_error_handler_messages(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        core = bot.get_cog("Core")

        async def shown(error, command="buy"):
            ctx = ctx_for(member(1))
            ctx.command = bot.get_command(command)
            await core.on_command_error(ctx, error)
            return sent_embed(ctx).description if ctx.send.called else None

        assert "laptop" in await shown(commands.CommandInvokeError(UserError("You need a laptop!")))
        assert "item" in await shown(
            commands.MissingRequiredArgument(commands.Parameter("item", inspect.Parameter.POSITIONAL_ONLY))
        )
        assert "Usage" in await shown(
            commands.MissingRequiredArgument(commands.Parameter("item", inspect.Parameter.POSITIONAL_ONLY))
        )
        assert "administrator" in await shown(commands.MissingPermissions(["administrator"]))
        assert "cooldown" in (await shown(commands.CommandOnCooldown(None, 75, commands.BucketType.user))).lower()
        assert "1m 15s" in await shown(commands.CommandOnCooldown(None, 75, commands.BucketType.user))
        assert "server" in await shown(commands.NoPrivateMessage())
        assert "counting" in await shown(CommandBlocked("Commands can't be used in the counting channel."))
        assert "permission" in await shown(commands.CommandInvokeError(MagicMock(spec=discord.Forbidden)))
        assert "went wrong" in await shown(commands.CommandInvokeError(ZeroDivisionError()))
        assert await shown(commands.CommandNotFound("nope")) is None  # silently ignored
        await bot.close()

    run(scenario())


# ------------------------------------------------------------------- levels
def test_levels_xp_cooldown_and_level_up(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        cog = bot.get_cog("Levels")
        cog.collection = MagicMock()
        cog.collection.find_one_and_update = AsyncMock(return_value={"id": 1, "xp": 50})

        msg = fake_message(1, 10, 5, "hello")
        await cog.on_message(msg)
        cog.collection.find_one_and_update.assert_awaited_once()
        msg.channel.send.assert_awaited()  # 40 -> 50 XP crosses into level 1

        again = fake_message(1, 10, 5, "hello again")
        await cog.on_message(again)  # inside the 30s cooldown
        assert cog.collection.find_one_and_update.await_count == 1

        # an outage must not crash the listener
        from pymongo.errors import ServerSelectionTimeoutError

        cog._last_xp.clear()
        cog.collection.find_one_and_update = AsyncMock(side_effect=ServerSelectionTimeoutError("down"))
        await cog.on_message(fake_message(1, 10, 5, "hi"))

        # ...and commands turn it into a friendly message
        cog.collection.find_one = AsyncMock(side_effect=ServerSelectionTimeoutError("down"))
        with pytest.raises(UserError):
            await call(cog, "rank", ctx_for(member(1)))
        await bot.close()

    run(scenario())


# ------------------------------------------------------------------ shutdown
@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM handler is POSIX-only")
def test_sigterm_flushes_data_and_closes(tmp_path, monkeypatch):
    async def scenario():
        bot = await make_bot(tmp_path, monkeypatch)
        bot.db.servers.ensure(1)["server_tokens"] = 123
        bot.db.servers.mark_dirty()  # the kind of change that is normally saved by the autosave timer
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.1)  # the loop runs signal handlers on its next iteration
        assert bot._shutdown_task is not None
        await asyncio.wait_for(bot._shutdown_task, timeout=5)
        assert bot.is_closed()
        assert json.loads((tmp_path / "serversettings.json").read_text())["1"]["server_tokens"] == 123

    run(scenario())


# -------------------------------------------------------------------- health
def test_health_endpoint():
    async def scenario():
        runner = await start_health_server(0)
        port = runner.addresses[0][1]
        async with aiohttp.ClientSession() as session:
            for path in ("/", "/health"):
                async with session.get(f"http://127.0.0.1:{port}{path}") as resp:
                    assert resp.status == 200 and "up" in await resp.text()
        await runner.cleanup()

    run(scenario())

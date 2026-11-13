"""Games: Tic-Tac-Toe, High-Low and Coin Flip - all driven by buttons."""

from __future__ import annotations

import random

import discord
from discord.ext import commands

from utils.economy import CURRENCY, parse_amount, roll_bankcap
from utils.errors import UserError

TTT_WAGER = 1_500  # winner takes this from the loser (or all they have, if less)

WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def find_winner(cells: list[str | None]) -> str | None:
    """Return ``'X'`` / ``'O'`` if someone has three in a row."""
    for a, b, c in WIN_LINES:
        if cells[a] is not None and cells[a] == cells[b] == cells[c]:
            return cells[a]
    return None


def make_highlow() -> tuple[int, int]:
    """Pick a secret number (1-100) and a hint that is never equal to it."""
    number = random.randint(1, 100)
    hint = number
    while hint == number:
        hint = min(100, max(1, random.randint(number - 20, number + 30)))
    return number, hint


# ------------------------------------------------------------- Tic-Tac-Toe
class TicTacToeButton(discord.ui.Button["TicTacToeView"]):
    def __init__(self, index: int) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=index // 3)
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.play(interaction, self)


class TicTacToeView(discord.ui.View):
    """One game. ``players[0]`` plays X, ``players[1]`` plays O."""

    def __init__(self, bot: commands.Bot, players: tuple[discord.Member, discord.Member]) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.players = players
        self.turn = random.randint(0, 1)
        self.cells: list[str | None] = [None] * 9
        self.message: discord.Message | None = None
        for index in range(9):
            self.add_item(TicTacToeButton(index))

    @property
    def current(self) -> discord.Member:
        return self.players[self.turn]

    def prompt(self) -> str:
        mark = "X" if self.turn == 0 else "O"
        return (
            f"❌ {self.players[0].mention} vs ⭕ {self.players[1].mention}\n"
            f"It's {self.current.mention}'s turn ({mark})."
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (p.id for p in self.players):
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return False
        if interaction.user.id != self.current.id:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return False
        return True

    async def play(self, interaction: discord.Interaction, button: TicTacToeButton) -> None:
        mark = "X" if self.turn == 0 else "O"
        self.cells[button.index] = mark
        button.label = mark
        button.style = discord.ButtonStyle.danger if mark == "X" else discord.ButtonStyle.success
        button.disabled = True

        winner = find_winner(self.cells)
        if winner is None and not all(self.cells):
            self.turn ^= 1
            await interaction.response.edit_message(content=self.prompt(), view=self)
            return

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        if winner is None:
            text = "🤝 It's a tie!"
        else:
            loser = self.players[1 - self.turn]
            text = f"🏆 {self.current.mention} wins!\n" + self._settle(self.current, loser)
        await interaction.response.edit_message(content=text, view=self)

    def _settle(self, winner: discord.Member, loser: discord.Member) -> str:
        users = self.bot.db.users
        won, lost = users.ensure(winner.id), users.ensure(loser.id)
        amount = min(TTT_WAGER, lost["wallet"])
        lost["wallet"] -= amount
        won["wallet"] += amount
        users.save()
        return f"{winner.display_name} won **{amount:,}** {CURRENCY} from {loser.display_name}."

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ The game timed out.", view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ High-Low
class HighLowView(discord.ui.View):
    def __init__(self, author: discord.abc.User, number: int, hint: int) -> None:
        super().__init__(timeout=30)
        self.author = author
        self.number = number
        self.hint = hint
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This isn't your game - start your own!", ephemeral=True)
            return False
        return True

    async def _answer(self, interaction: discord.Interaction, said_higher: bool) -> None:
        correct = (self.number > self.hint) == said_higher
        text = (
            f"✅ Correct, the number was **{self.number}**!"
            if correct
            else f"❌ Incorrect, the number was **{self.number}**."
        )
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="Higher", emoji="⬆️", style=discord.ButtonStyle.primary)
    async def higher(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._answer(interaction, True)

    @discord.ui.button(label="Lower", emoji="⬇️", style=discord.ButtonStyle.primary)
    async def lower(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._answer(interaction, False)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(content=f"⌛ Too slow! The number was **{self.number}**.", view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ Coin flip
class CoinFlipView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author: discord.abc.User, bet: int) -> None:
        super().__init__(timeout=30)
        self.bot = bot
        self.author = author
        self.bet = bet
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This isn't your coin flip!", ephemeral=True)
            return False
        return True

    async def _flip(self, interaction: discord.Interaction, choice: str) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()

        acc = self.bot.db.users.ensure(self.author.id)
        if acc["wallet"] < self.bet:  # they spent it while the buttons were up
            await interaction.response.edit_message(
                content="😬 You no longer have enough tokens for that bet.", view=self
            )
            return

        landed = random.choice(("heads", "tails"))
        if landed == choice:
            acc["wallet"] += self.bet
            text = f"🪙 It landed on **{landed}**! 🥳 You won **{self.bet:,}** {CURRENCY}."
        else:
            acc["wallet"] -= self.bet
            text = f"🪙 It landed on **{landed}**. 😢 You lost **{self.bet:,}** {CURRENCY}."
        roll_bankcap(acc, 1 / 3)
        self.bot.db.users.save()
        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="Heads", style=discord.ButtonStyle.secondary)
    async def heads(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._flip(interaction, "heads")

    @discord.ui.button(label="Tails", style=discord.ButtonStyle.secondary)
    async def tails(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._flip(interaction, "tails")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ Coin flip timed out - no tokens were lost.", view=self)
            except discord.HTTPException:
                pass


class Games(commands.Cog):
    """Games to play with friends (some with Mission Tokens on the line)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ttt", aliases=["tictactoe"])
    @commands.guild_only()
    async def tic_tac_toe(self, ctx: commands.Context, opponent: discord.Member) -> None:
        """Play Tic-Tac-Toe against someone. The winner takes up to 1,500 tokens."""
        if opponent.bot or opponent == ctx.author:
            raise UserError("Pick another (human) member to play against.")
        view = TicTacToeView(self.bot, (ctx.author, opponent))
        view.message = await ctx.send(view.prompt(), view=view)

    @commands.command(name="hl", aliases=["highlow"])
    async def high_low(self, ctx: commands.Context) -> None:
        """Guess whether a secret number (1-100) is higher or lower than the hint."""
        number, hint = make_highlow()
        view = HighLowView(ctx.author, number, hint)
        view.message = await ctx.send(
            f"The secret number is between 1 and 100. Your hint is **{hint}**.\nIs it higher or lower?", view=view
        )

    @commands.command(name="cf", aliases=["coinflip"])
    async def coin_flip(self, ctx: commands.Context, amount: str) -> None:
        """Bet tokens on a coin flip (amount or `max`). Pick heads or tails with the buttons."""
        acc = self.bot.db.users.ensure(ctx.author.id)
        bet = parse_amount(amount, acc["wallet"])
        view = CoinFlipView(self.bot, ctx.author, bet)
        view.message = await ctx.send(f"🪙 Betting **{bet:,}** {CURRENCY}. Heads or tails?", view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))

"""Economy: Mission Tokens, bank, shop, inventory and ways to earn."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.economy import CURRENCY, inv_add, inv_amount, parse_amount, roll_bankcap
from utils.embeds import requested_by
from utils.errors import UserError
from utils.store import JsonStore

# ----------------------------------------------------------------- game data
# Tweak prices / rewards here.
DAILY_REWARD = 1_000
BANKCOIN_CAPACITY = 4_000
SELL_RATIO = 0.5  # shop items sell back for this fraction of their price
LOTTERY_WIN_CHANCE = 1 / 301
LOTTERY_JACKPOT = 100_000
LOTTERY_LOSS = 10


@dataclass(frozen=True)
class ShopItem:
    id: str
    name: str
    price: int
    description: str


SHOP: dict[str, ShopItem] = {
    item.id: item
    for item in (
        ShopItem("rolexwatch", "RolexWatch", 100_000, "Pure showing off"),
        ShopItem("laptop", "Laptop", 1_000, "Lets you post memes (`pm`)"),
        ShopItem("multiplier", "Multiplier", 10_000, "`use` it for +1x on everything you earn"),
        ShopItem("fishingrod", "FishingRod", 5_000, "Lets you `fish`"),
        ShopItem("bankcoin", "BankCoin", 5_500, f"`use` it for +{BANKCOIN_CAPACITY:,} bank capacity"),
        ShopItem("lotteryticket", "LotteryTicket", 650, f"`use` it for a small chance at {LOTTERY_JACKPOT:,}"),
    )
}

# fish id -> (display name, sell price)
FISH: dict[str, tuple[str, int]] = {
    "commonfish": ("Common Fish", 100),
    "uncommonfish": ("Uncommon Fish", 250),
    "rarefish": ("Rare Fish", 1_000),
}

SEARCH_FINDS = (
    "You searched the bin and found **{n}** Mission Tokens",
    "You searched the street and found **{n}** Mission Tokens",
    "You searched through your DMs and found **{n}** Mission Tokens",
    "You searched your bed and found **{n}** Mission Tokens, wow!",
    "You searched your clothes and found **{n}** Mission Tokens",
    "You searched your crush's DMs and found **{n}** Mission Tokens",
)
SEARCH_DEATHS = (
    "You walked out onto traffic looking for money!",
    "A dog bit you while you were looking for money",
    "You forgot to breathe while looking for money",
    "You went into a dragon's lair looking for money and the dragon killed you and made you its food",
    "Awasthi ate you and farted when you were asking him for money!",
    "Bipul used his annoying voice to kill you while you were asking him for money",
)


def sell_price(item_id: str) -> int | None:
    """What one of ``item_id`` sells for, or ``None`` if it can't be sold."""
    if item_id in SHOP:
        return int(SHOP[item_id].price * SELL_RATIO)
    if item_id in FISH:
        return FISH[item_id][1]
    return None


def display_name(item_id: str) -> str:
    if item_id in SHOP:
        return SHOP[item_id].name
    if item_id in FISH:
        return FISH[item_id][0]
    return item_id


def roll_fish() -> tuple[str, int] | None:
    """One cast of the line -> ``(fish_id, how_many)`` or ``None`` for an empty hook."""
    if random.randint(1, 3) == 2:  # 1 in 3 casts catch nothing
        return None
    roll = random.randint(1, 10)
    if roll == 5:
        return "rarefish", 1
    if roll in (1, 2, 3):
        return "uncommonfish", 2
    return "commonfish", 3


class Economy(commands.Cog):
    """Earn, save, spend and trade Mission Tokens."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def users(self) -> JsonStore:
        return self.bot.db.users

    def account(self, member: discord.abc.User) -> dict:
        return self.users.ensure(member.id)

    # ------------------------------------------------------------- balances
    @commands.command(aliases=["bal"])
    async def balance(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show your (or someone else's) wallet and bank."""
        member = member or ctx.author
        acc = self.account(member)
        embed = discord.Embed(title=f"{member.display_name}'s balance", colour=discord.Colour.green())
        embed.add_field(name="Wallet 👛", value=f"{acc['wallet']:,}")
        embed.add_field(name="Bank 🏦", value=f"{acc['bank']:,} / {acc['bankcap']:,}")
        embed.add_field(name="Net worth", value=f"{acc['wallet'] + acc['bank']:,}")
        await ctx.send(embed=requested_by(embed, ctx))

    @commands.command(aliases=["dep"])
    async def deposit(self, ctx: commands.Context, amount: str) -> None:
        """Move tokens from your wallet to your bank (`all` = as much as fits)."""
        acc = self.account(ctx.author)
        room = acc["bankcap"] - acc["bank"]
        if room <= 0:
            raise UserError("Your bank is full! Use a BankCoin to raise its capacity.")
        value = parse_amount(amount, acc["wallet"], all_amount=min(acc["wallet"], room))
        if value > room:
            raise UserError(f"Your bank only has room for **{room:,}** more.")
        acc["wallet"] -= value
        acc["bank"] += value
        self.users.save()
        await ctx.send(f"🏦 Deposited **{value:,}** {CURRENCY}.")

    @commands.command(aliases=["with"])
    async def withdraw(self, ctx: commands.Context, amount: str) -> None:
        """Move tokens from your bank to your wallet (`all` = everything)."""
        acc = self.account(ctx.author)
        value = parse_amount(amount, acc["bank"])
        acc["bank"] -= value
        acc["wallet"] += value
        self.users.save()
        await ctx.send(f"👛 Withdrew **{value:,}** {CURRENCY}.")

    @commands.command(name="send", aliases=["give", "pay"])
    async def send_money(self, ctx: commands.Context, member: discord.Member, amount: str) -> None:
        """Give some of your wallet tokens to another member."""
        if member.bot or member == ctx.author:
            raise UserError("Pick another (human) member.")
        sender, receiver = self.account(ctx.author), self.account(member)
        value = parse_amount(amount, sender["wallet"])
        sender["wallet"] -= value
        receiver["wallet"] += value
        self.users.save()
        await ctx.send(f"💸 {ctx.author.mention} sent **{value:,}** {CURRENCY} to {member.mention}.")

    # --------------------------------------------------------------- earning
    @commands.command()
    async def daily(self, ctx: commands.Context) -> None:
        """Claim your daily reward (once every 24 hours)."""
        acc = self.account(ctx.author)
        now = time.time()
        ready_at = acc["last_daily"] + 86_400
        if now < ready_at:
            when = discord.utils.format_dt(datetime.fromtimestamp(ready_at, tz=timezone.utc), "R")
            raise UserError(f"You already claimed your daily reward. Come back {when}.")
        reward = DAILY_REWARD * acc["multiplier"]
        acc["wallet"] += reward
        acc["last_daily"] = now
        self.users.save()
        await ctx.send(f"🎁 You claimed your daily reward of **{reward:,}** {CURRENCY}!")

    @commands.command()
    @commands.cooldown(1, 45, commands.BucketType.user)
    async def beg(self, ctx: commands.Context) -> None:
        """Beg for a few tokens."""
        if random.random() < 0.3:
            await ctx.send("🥺 Nobody gave you anything. Maybe try again later.")
            return
        acc = self.account(ctx.author)
        earned = random.randint(20, 200) * acc["multiplier"]
        acc["wallet"] += earned
        self.users.save()
        await ctx.send(f"🙏 A kind stranger gave you **{earned:,}** {CURRENCY}.")

    @commands.command()
    @commands.cooldown(2, 600, commands.BucketType.user)
    async def search(self, ctx: commands.Context) -> None:
        """Search for money... or die trying (you lose your wallet if you do)."""
        acc = self.account(ctx.author)
        roll_bankcap(acc, 1 / 3)
        embed = discord.Embed(title=CURRENCY, description=f"Use **`{ctx.clean_prefix}help economy`** for more commands")
        if random.random() < 0.5:
            earned = random.randint(200, 2000) * acc["multiplier"]
            acc["wallet"] += earned
            embed.colour = discord.Colour.green()
            embed.add_field(name="You Searched", value=random.choice(SEARCH_FINDS).format(n=f"{earned:,}"))
        else:
            acc["wallet"] = 0
            embed.colour = discord.Colour.dark_red()
            embed.add_field(name="You Died", value=random.choice(SEARCH_DEATHS))
        self.users.save()
        await ctx.send(embed=embed)

    @commands.command(aliases=["pm"])
    @commands.cooldown(2, 240, commands.BucketType.user)
    async def postmeme(self, ctx: commands.Context) -> None:
        """Post a meme to earn tokens (needs a Laptop)."""
        acc = self.account(ctx.author)
        if not inv_amount(acc, "laptop"):
            raise UserError("You need a laptop! Buy one from the `shop`.")
        if random.randint(1, 3) == 2:
            embed = discord.Embed(
                title="Post Meme",
                description="You posted a horrible meme and earned 0 Mission Tokens",
                colour=discord.Colour.red(),
            )
        else:
            earned = random.randrange(543) * acc["multiplier"]
            acc["wallet"] += earned
            self.users.save()
            embed = discord.Embed(
                title="Post Meme",
                description=f"You posted a meme and earned **{earned:,}** Mission Tokens.",
                colour=discord.Colour.green(),
            )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def fish(self, ctx: commands.Context) -> None:
        """Go fishing (needs a FishingRod). Sell what you catch with `sell`."""
        acc = self.account(ctx.author)
        if not inv_amount(acc, "fishingrod"):
            raise UserError("You need a fishing rod! Buy one from the `shop`.")
        catch = roll_fish()
        if catch is None:
            embed = discord.Embed(title="You Suck", description="You reeled up nothing.", colour=discord.Colour.red())
        else:
            fish_id, count = catch
            inv_add(acc, fish_id, count)
            self.users.save()
            embed = discord.Embed(
                title="Caught",
                description=f"You caught {count} **{display_name(fish_id)}**.",
                colour=discord.Colour.green(),
            )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------ shop & items
    @commands.command()
    async def shop(self, ctx: commands.Context) -> None:
        """See what's for sale."""
        embed = discord.Embed(title="Shop", colour=discord.Colour.orange())
        for item in SHOP.values():
            embed.add_field(name=f"{item.name} - {item.price:,} {CURRENCY}", value=item.description, inline=False)
        embed.set_footer(text=f"Buy with {ctx.clean_prefix}buy <item> [amount]")
        await ctx.send(embed=embed)

    @commands.command()
    async def buy(self, ctx: commands.Context, item: str, amount: int = 1) -> None:
        """Buy an item from the shop."""
        shop_item = SHOP.get(item.lower())
        if shop_item is None:
            raise UserError(f"That item isn't in the shop. See `{ctx.clean_prefix}shop`.")
        if amount < 1:
            raise UserError("The amount must be at least 1.")
        acc = self.account(ctx.author)
        cost = shop_item.price * amount
        if acc["wallet"] < cost:
            raise UserError(f"You need **{cost:,}** {CURRENCY} in your wallet to buy {amount}x {shop_item.name}.")
        acc["wallet"] -= cost
        inv_add(acc, shop_item.id, amount)
        roll_bankcap(acc, 1 / 6)
        self.users.save()
        await ctx.send(f"🛒 You just bought **{amount}x {shop_item.name}** for **{cost:,}** {CURRENCY}.")

    @commands.command()
    async def sell(self, ctx: commands.Context, item: str, amount: str = "1") -> None:
        """Sell items from your inventory (`all` sells the whole stack)."""
        item_id = item.lower()
        price = sell_price(item_id)
        if price is None:
            raise UserError("That item can't be sold.")
        acc = self.account(ctx.author)
        owned = inv_amount(acc, item_id)
        if not owned:
            raise UserError(f"You don't have any {display_name(item_id)}.")
        count = parse_amount(amount, owned)
        earned = price * count
        inv_add(acc, item_id, -count)
        acc["wallet"] += earned
        self.users.save()
        await ctx.send(f"💰 You sold **{count}x {display_name(item_id)}** for **{earned:,}** {CURRENCY}.")

    @commands.command(aliases=["inv"])
    async def inventory(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show your (or someone else's) inventory."""
        member = member or ctx.author
        acc = self.account(member)
        embed = discord.Embed(title=f"{member.display_name}'s inventory", colour=discord.Colour.green())
        if not acc["inventory"]:
            embed.description = "Nothing here yet - check the `shop`!"
        for entry in acc["inventory"]:
            embed.add_field(
                name=f"{display_name(entry['item'])} x{entry['amount']:,}", value=f"id: `{entry['item']}`", inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def use(self, ctx: commands.Context, item: str, amount: int = 1) -> None:
        """Use an item: bankcoin, multiplier or lotteryticket."""
        item_id = item.lower()
        acc = self.account(ctx.author)
        if item_id not in ("bankcoin", "multiplier", "lotteryticket"):
            if item_id in SHOP or item_id in FISH:
                raise UserError(
                    "That item can't be used - it does its job just by sitting in your inventory (or sell it)."
                )
            raise UserError("I don't know that item.")
        owned = inv_amount(acc, item_id)
        if not owned:
            raise UserError(f"You don't have any {display_name(item_id)}.")
        if not 1 <= amount <= owned:
            raise UserError(f"You have **{owned}** - pick an amount between 1 and {owned}.")

        inv_add(acc, item_id, -amount)
        roll_bankcap(acc, 1 / 6)
        embed = discord.Embed(title=CURRENCY, colour=discord.Colour.green())

        if item_id == "bankcoin":
            acc["bankcap"] += BANKCOIN_CAPACITY * amount
            added = BANKCOIN_CAPACITY * amount
            text = (
                f"**{ctx.author.display_name}** used :coin: **{amount}** BankCoin(s).\n"
                f"**{added:,}** was added to your bank capacity, putting you at **{acc['bankcap']:,}**."
            )
        elif item_id == "multiplier":
            acc["multiplier"] += amount
            text = (
                f"**{ctx.author.display_name}** used **{amount}** Multiplier(s). "
                f"You are now at **{acc['multiplier']}x**."
            )
        else:  # lotteryticket
            wins = sum(random.random() < LOTTERY_WIN_CHANCE for _ in range(amount))
            lost = min((amount - wins) * LOTTERY_LOSS, acc["wallet"])
            acc["wallet"] += wins * LOTTERY_JACKPOT - lost
            text = f"You scratched **{amount}** ticket(s)."
            if wins:
                text += f"\n🥳 You won **{wins * LOTTERY_JACKPOT:,}** {CURRENCY}!"
            if lost:
                text += f"\nYou lost **{lost:,}** more {CURRENCY}."
            if not wins and not lost:
                text += "\nNo luck this time."
            embed.colour = discord.Colour.gold() if wins else discord.Colour.red()

        embed.add_field(name="Item Used", value=text)
        self.users.save()
        await ctx.send(embed=embed)

    # ----------------------------------------------------------- social stats
    @commands.command(aliases=["lb"])
    async def leaderboard(self, ctx: commands.Context, top: int = 5) -> None:
        """Show the richest users (wallet + bank). Default top 5, max 15."""
        top = max(1, min(top, 15))
        ranked = sorted(self.users.data.items(), key=lambda kv: kv[1]["wallet"] + kv[1]["bank"], reverse=True)[:top]
        lines = [
            f"`{i}.` <@{user_id}> - **{acc['wallet'] + acc['bank']:,}** {CURRENCY}"
            for i, (user_id, acc) in enumerate(ranked, start=1)
        ]
        embed = discord.Embed(
            title=f"Top {len(ranked)} Richest Users",
            description="Based on wallet + bank.\n\n" + "\n".join(lines),
            colour=discord.Colour.green(),
        )
        await ctx.send(embed=embed)

    @commands.command(aliases=["prof"])
    async def profile(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show a member's profile."""
        member = member or ctx.author
        acc = self.account(member)
        reports = self.bot.db.reports.get(member.id)
        warns = len(reports["warns"]) if reports else 0
        embed = discord.Embed(
            title=f"{member.display_name}'s profile",
            description=f"Use **`{ctx.clean_prefix}help`** for more commands.",
            colour=member.colour,
        )
        embed.add_field(
            name="Economy! 💰",
            value=(
                f"Wallet - **{acc['wallet']:,}** 👛\n"
                f"Bank - **{acc['bank']:,}** 🏦\n"
                f"Multiplier - **{acc['multiplier']}x**"
            ),
            inline=False,
        )
        embed.add_field(name="Warns ❗", value=f"`{warns}`", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=requested_by(embed, ctx))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))

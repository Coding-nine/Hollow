"""Internal plumbing: error handling, presence, autosave and owner tools."""

from __future__ import annotations

import itertools
import logging

import discord
from discord.ext import commands, tasks

from utils.embeds import error_embed, humanize_seconds, success_embed
from utils.errors import CommandBlocked, UserError

log = logging.getLogger(__name__)


class Core(commands.Cog):
    """Internal bot plumbing."""

    hidden = True  # not shown in the help command

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._statuses = itertools.cycle(("help", "servers"))

    async def cog_load(self) -> None:
        self.autosave.start()
        self.rotate_presence.start()

    async def cog_unload(self) -> None:
        self.autosave.cancel()
        self.rotate_presence.cancel()
        self.bot.db.flush()

    # ------------------------------------------------------------ background
    @tasks.loop(seconds=15)
    async def autosave(self) -> None:
        self.bot.db.flush()

    @tasks.loop(seconds=60)
    async def rotate_presence(self) -> None:
        if next(self._statuses) == "help":
            activity = discord.Activity(type=discord.ActivityType.listening, name="$help")
        else:
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"over {len(self.bot.guilds)} servers")
        await self.bot.change_presence(status=discord.Status.online, activity=activity)

    @rotate_presence.before_loop
    async def _wait_until_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s) - in %d servers", self.bot.user, self.bot.user.id, len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.bot.db.servers.ensure(guild.id)
        self.bot.db.servers.mark_dirty()

        channel = discord.utils.get(guild.text_channels, name="general") or guild.system_channel
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            return
        await channel.send(
            "Hello! I am Mission-Bot - you may nick me as you like. I'm a custom-made bot "
            "with lots of features. To get started, use `$help`."
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Reply with the prefix when someone only @mentions the bot."""
        if message.author.bot or message.guild is None or self.bot.user is None:
            return
        if message.content.strip() in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            prefix = self.bot.db.servers.ensure(message.guild.id)["prefix"]
            await message.channel.send(f"My prefix here is `{prefix}` - try `{prefix}help`.")

    # ---------------------------------------------------------- error handler
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        text: str | None = None
        if isinstance(error, (UserError, CommandBlocked)):
            text = str(error)
        elif isinstance(error, commands.MissingRequiredArgument):
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            text = f"You're missing `{error.param.name}`.\nUsage: `{usage}`"
        elif isinstance(error, commands.MissingPermissions):
            needed = ", ".join(p.replace("_", " ") for p in error.missing_permissions)
            text = f"You need the **{needed}** permission to do that."
        elif isinstance(error, commands.BotMissingPermissions):
            needed = ", ".join(p.replace("_", " ") for p in error.missing_permissions)
            text = f"I need the **{needed}** permission to do that."
        elif isinstance(error, commands.CommandOnCooldown):
            text = f"Still on cooldown - try again in **{humanize_seconds(error.retry_after)}**."
        elif isinstance(error, commands.NoPrivateMessage):
            text = "That command only works in a server."
        elif isinstance(error, commands.NotOwner):
            text = "Only the bot owner can use that."
        elif isinstance(error, commands.CheckFailure):
            text = "You can't use that command here."
        elif isinstance(error, commands.UserInputError):  # bad / unparsable arguments
            text = str(error) or "I couldn't understand that input."
        elif isinstance(error, discord.Forbidden):
            text = "I don't have permission to do that. Check that my role is above the target's role."

        if text is None:
            log.error("Unhandled error in command %s", ctx.command, exc_info=error)
            text = "Something went wrong on my end. It has been logged."

        try:
            await ctx.send(embed=error_embed(text))
        except discord.HTTPException:
            pass  # can't send in this channel - nothing more to do

    # ------------------------------------------------------------ owner tools
    @commands.command(hidden=True)
    @commands.is_owner()
    async def load(self, ctx: commands.Context, extension: str) -> None:
        """Load a cog, e.g. `load economy`."""
        await self.bot.load_extension(f"cogs.{extension}")
        await ctx.send(embed=success_embed(f"Loaded `{extension}`."))

    @commands.command(hidden=True)
    @commands.is_owner()
    async def unload(self, ctx: commands.Context, extension: str) -> None:
        """Unload a cog, e.g. `unload economy`."""
        await self.bot.unload_extension(f"cogs.{extension}")
        await ctx.send(embed=success_embed(f"Unloaded `{extension}`."))

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, extension: str) -> None:
        """Reload a cog after editing it, e.g. `reload economy`."""
        await self.bot.reload_extension(f"cogs.{extension}")
        await ctx.send(embed=success_embed(f"Reloaded `{extension}`."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Core(bot))

"""Mission Bot - entry point.   Run with:  python bot.py"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv

from utils.health import start_health_server
from utils.store import Database

log = logging.getLogger("missionbot")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PREFIX = "$"


class MissionBot(commands.Bot):
    def __init__(self, db: Database) -> None:
        intents = discord.Intents.default()
        # Both of these are *privileged* intents: switch them on for your bot at
        # https://discord.com/developers/applications -> Bot -> Privileged Gateway Intents
        intents.message_content = True  # prefix commands, counting game, automod
        intents.members = True  # welcome/leave messages, member lookups

        super().__init__(
            command_prefix=DEFAULT_PREFIX,  # real prefix is resolved in get_prefix()
            intents=intents,
            help_command=None,  # we ship our own (cogs/help.py)
            case_insensitive=True,
            # Never let echoed user text ping @everyone or roles.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.db = db
        self._health_runner: web.AppRunner | None = None
        self._shutdown_task: asyncio.Task | None = None

    # ---------------------------------------------------------------- prefix
    async def get_prefix(self, message: discord.Message) -> list[str]:
        prefix = DEFAULT_PREFIX
        if message.guild is not None:
            record = self.db.servers.get(message.guild.id)
            if record is not None:
                prefix = record["prefix"]
        return commands.when_mentioned_or(prefix)(self, message)

    # -------------------------------------------------------------- lifecycle
    async def setup_hook(self) -> None:
        for path in sorted((BASE_DIR / "cogs").glob("*.py")):
            if path.name.startswith("_"):
                continue
            extension = f"cogs.{path.stem}"
            try:
                await self.load_extension(extension)
            except Exception:  # one broken cog must not stop the whole bot
                log.exception("Failed to load %s", extension)

        # Hosts/containers stop a bot with SIGTERM; shut down cleanly so data gets saved.
        if sys.platform != "win32":
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self._request_shutdown)

        port = os.getenv("PORT") or os.getenv("HEALTH_PORT")
        if port:
            self._health_runner = await start_health_server(int(port))
            log.info("Health endpoint listening on port %s", port)

    def _request_shutdown(self) -> None:
        log.info("SIGTERM received - shutting down")
        self._shutdown_task = asyncio.ensure_future(self.close())

    async def close(self) -> None:
        self.db.flush()  # make sure nothing is lost on shutdown
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await super().close()


def main() -> None:
    load_dotenv()  # reads .env if present; real environment variables win
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit(
            "DISCORD_TOKEN is not set.\n"
            "Copy .env.example to .env and paste your bot token in it "
            "(or set DISCORD_TOKEN as an environment variable on your host)."
        )

    db = Database(Path(os.getenv("DATA_DIR") or BASE_DIR / "data"))
    db.load()
    MissionBot(db).run(token)  # also sets up logging


if __name__ == "__main__":
    main()

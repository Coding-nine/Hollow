"""Custom exceptions whose messages are safe to show to users as-is."""

from __future__ import annotations

from discord.ext import commands


class UserError(commands.CommandError):
    """Raised inside a command when the user did something we can explain.

    The global error handler (cogs/core.py) shows ``str(error)`` in a red embed,
    so commands can just ``raise UserError("You need a laptop!")``.
    """


class CommandBlocked(commands.CheckFailure):
    """Raised by a check that wants to explain *why* a command was blocked."""

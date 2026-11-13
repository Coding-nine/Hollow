"""Pure helpers shared by the economy and games cogs (no Discord objects here)."""

from __future__ import annotations

import random

from utils.errors import UserError

CURRENCY = "Mission Tokens"


def parse_amount(raw: str, available: int, *, all_amount: int | None = None) -> int:
    """Turn user input like ``500``, ``1,000``, ``all`` or ``max`` into an int.

    ``available`` is the most the user may use. ``all_amount`` lets a command say
    what "all" means when that differs from ``available`` (e.g. a nearly full bank).
    """
    text = raw.strip().lower().replace(",", "")
    if text in {"all", "max"}:
        value = available if all_amount is None else all_amount
    elif text.isascii() and text.isdigit():
        value = int(text)
    else:
        raise UserError("The amount must be a whole number (or `all`).")

    if value <= 0:
        raise UserError("The amount must be at least 1.")
    if value > available:
        raise UserError(f"You only have **{available:,}**.")
    return value


def inv_amount(record: dict, item: str) -> int:
    for entry in record["inventory"]:
        if entry["item"] == item:
            return entry["amount"]
    return 0


def inv_add(record: dict, item: str, delta: int) -> None:
    """Add (or, with a negative ``delta``, remove) items. Empty stacks disappear."""
    for entry in record["inventory"]:
        if entry["item"] == item:
            entry["amount"] += delta
            if entry["amount"] <= 0:
                record["inventory"].remove(entry)
            return
    if delta > 0:
        record["inventory"].append({"item": item, "amount": delta})


def roll_bankcap(record: dict, chance: float) -> None:
    """Little hidden perk from the original bot: using economy commands sometimes
    raises your bank capacity by 200-499."""
    if random.random() < chance:
        record["bankcap"] += random.randrange(200, 500)

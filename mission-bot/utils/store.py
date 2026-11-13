"""Tiny JSON-file persistence layer.

The old bot re-read and re-wrote a whole JSON file on every call, which meant
two overlapping commands could overwrite each other with stale data. Here each
file is loaded once into memory, mutated in place (safe: asyncio is
single-threaded and none of the mutations ``await`` half-way), and written back
atomically (temp file + rename) so a crash can never leave a half-written file.

    record = db.users.ensure(member.id)   # creates the record if needed
    record["wallet"] += 100
    db.users.save()                        # write now (important changes)
    db.servers.mark_dirty()                # write within a few seconds (frequent, minor changes)
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

Record = dict[str, Any]


class JsonStore:
    """A ``{str(id): record}`` mapping persisted to one JSON file."""

    def __init__(
        self,
        path: Path,
        defaults: Callable[[], Record],
        normalize: Callable[[Record], None] | None = None,
    ) -> None:
        self.path = path
        self._defaults = defaults
        self._normalize = normalize
        self.data: dict[str, Record] = {}
        self.dirty = False

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        raw: Any = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            except (json.JSONDecodeError, OSError):
                backup = self.path.with_name(f"{self.path.stem}.corrupt-{int(time.time())}.json")
                log.exception("Could not read %s - moving it to %s and starting empty", self.path, backup)
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                raw = {}
        if not isinstance(raw, dict):
            raw = {}

        changed = False
        for key, record in raw.items():
            if not isinstance(record, dict):
                changed = True
                continue
            before = json.dumps(record, sort_keys=True)
            self._prepare(record)
            changed = changed or json.dumps(record, sort_keys=True) != before
            self.data[str(key)] = record

        if changed:  # an old-format file was upgraded - persist the upgrade
            self.save()

    def _prepare(self, record: Record) -> None:
        for key, value in self._defaults().items():
            record.setdefault(key, value)
        if self._normalize is not None:
            self._normalize(record)

    # ---------------------------------------------------------------- access
    def get(self, key: int | str) -> Record | None:
        """Return the live record, or ``None`` (does not create anything)."""
        return self.data.get(str(key))

    def ensure(self, key: int | str) -> Record:
        """Return the live record, creating it with default values if missing."""
        key = str(key)
        record = self.data.get(key)
        if record is None:
            record = {}
            self._prepare(record)
            self.data[key] = record
            self.dirty = True
        return record

    # --------------------------------------------------------------- persist
    def mark_dirty(self) -> None:
        self.dirty = True

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=4, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self.dirty = False

    def flush(self) -> None:
        """Save only if something changed since the last save."""
        if self.dirty:
            self.save()


# --------------------------------------------------------------------------
# Record shapes. ``defaults`` fills in anything missing (so records created by
# older versions of the bot keep working), ``normalize`` repairs old formats.
# --------------------------------------------------------------------------
def _user_defaults() -> Record:
    return {
        "wallet": 1000,
        "bank": 0,
        "bankcap": 10000,
        "multiplier": 1,
        "inventory": [],
        "last_daily": 0,
    }


def _normalize_user(record: Record) -> None:
    # Old accounts stored an empty dict ({}) when the inventory was empty.
    raw = record.get("inventory")
    if isinstance(raw, dict):
        raw = [{"item": k, "amount": v} for k, v in raw.items()]
    elif not isinstance(raw, list):
        raw = []

    merged: dict[str, int] = {}
    for entry in raw:
        if not isinstance(entry, dict) or "item" not in entry:
            continue
        item = str(entry["item"]).lower().replace(" ", "")  # "Rare Fish" -> "rarefish"
        try:
            amount = int(entry.get("amount", 0))
        except (TypeError, ValueError):
            continue
        if amount > 0:
            merged[item] = merged.get(item, 0) + amount
    record["inventory"] = [{"item": k, "amount": v} for k, v in merged.items()]


def _server_defaults() -> Record:
    return {
        "prefix": "$",
        "welcome_channel": None,
        "leave_channel": None,
        "automod": False,
        "server_tokens": 0,
    }


def _normalize_server(record: Record) -> None:
    # Old data used the string "None" (or a role name) where an ID was expected.
    for key in ("welcome_channel", "leave_channel"):
        if not isinstance(record.get(key), int):
            record[key] = None
    record["automod"] = bool(record.get("automod"))
    if not isinstance(record.get("prefix"), str) or not record["prefix"]:
        record["prefix"] = "$"


def _counting_defaults() -> Record:
    return {"counting_channel": None, "count": 1, "old_author": None, "saves": 0}


def _normalize_counting(record: Record) -> None:
    if not isinstance(record.get("counting_channel"), int):
        record["counting_channel"] = None
    if not isinstance(record.get("count"), int) or record["count"] < 1:
        record["count"] = 1
    if not isinstance(record.get("saves"), int) or record["saves"] < 0:
        record["saves"] = 0


class Database:
    """All the bot's JSON files in one place (``bot.db``)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.users = JsonStore(directory / "users.json", _user_defaults, _normalize_user)
        self.servers = JsonStore(directory / "serversettings.json", _server_defaults, _normalize_server)
        self.counting = JsonStore(directory / "counting.json", _counting_defaults, _normalize_counting)
        self.count_scores = JsonStore(directory / "countingscore.json", lambda: {"correct": 0, "wrong": 0, "score": 0})
        self.reports = JsonStore(directory / "reports.json", lambda: {"warns": []})

    @property
    def stores(self) -> tuple[JsonStore, ...]:
        return (self.users, self.servers, self.counting, self.count_scores, self.reports)

    def load(self) -> None:
        for store in self.stores:
            store.load()

    def flush(self) -> None:
        for store in self.stores:
            store.flush()

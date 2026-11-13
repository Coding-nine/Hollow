"""Tests for the pure logic: storage, amounts, counting, tic-tac-toe, automod, levels."""

from __future__ import annotations

import json
import random

import pytest

from cogs.counting import process_count
from cogs.economy import FISH, SHOP, roll_fish, sell_price
from cogs.games import find_winner, make_highlow
from cogs.levels import level_for, progress_bar, xp_for_level
from cogs.moderation import compile_banned_words, find_banned_word, parse_banned_words
from utils.economy import inv_add, inv_amount, parse_amount
from utils.errors import UserError
from utils.store import Database


# ------------------------------------------------------------------- storage
def test_legacy_data_is_upgraded(tmp_path):
    """Old files (empty-dict inventories, "None" strings, missing fields) load cleanly."""
    (tmp_path / "users.json").write_text(
        json.dumps(
            {
                "1": {"wallet": 5, "bank": 0, "inventory": {}, "bankcap": 10000, "multiplier": 1, "pet": {}},
                "2": {
                    "wallet": 9,
                    "bank": 1,
                    "inventory": [
                        {"item": "laptop", "amount": 1},
                        {"item": "Rare Fish", "amount": 2},
                        {"item": "bankcoin", "amount": 0},
                    ],
                    "bankcap": 10000,
                    "multiplier": 2,
                },
            }
        )
    )
    (tmp_path / "serversettings.json").write_text(
        json.dumps(
            {
                "9": {
                    "welcome_channel": "None",
                    "leave_channel": 123,
                    "prefix": "&",
                    "muted": "Nothing",
                    "automod": True,
                    "server_tokens": 8,
                }
            }
        )
    )
    (tmp_path / "counting.json").write_text(
        json.dumps({"9": {"counting_channel": None, "count": 1, "old_author": None, "saves": 0}})
    )

    db = Database(tmp_path)
    db.load()

    assert db.users.get(1)["inventory"] == []
    assert db.users.get(1)["last_daily"] == 0  # new field filled in
    assert db.users.get(2)["inventory"] == [{"item": "laptop", "amount": 1}, {"item": "rarefish", "amount": 2}]
    assert db.servers.get(9)["welcome_channel"] is None
    assert db.servers.get(9)["leave_channel"] == 123
    assert db.servers.get(9)["prefix"] == "&"
    # the upgrade was written back to disk
    assert json.loads((tmp_path / "users.json").read_text())["1"]["inventory"] == []


def test_save_roundtrip_and_new_records(tmp_path):
    db = Database(tmp_path)
    db.load()
    acc = db.users.ensure(42)
    assert acc["wallet"] == 1000 and acc["bankcap"] == 10000
    acc["wallet"] = 77
    db.users.save()
    assert not list(tmp_path.glob("*.tmp"))  # atomic write left no temp file

    again = Database(tmp_path)
    again.load()
    assert again.users.get(42)["wallet"] == 77


def test_corrupt_file_is_set_aside(tmp_path):
    (tmp_path / "users.json").write_text("{ this is not json")
    db = Database(tmp_path)
    db.load()
    assert db.users.data == {}
    assert list(tmp_path.glob("users.corrupt-*.json"))


def test_flush_only_writes_when_dirty(tmp_path):
    db = Database(tmp_path)
    db.load()
    db.flush()
    assert not (tmp_path / "users.json").exists()
    db.users.ensure(1)
    db.flush()
    assert (tmp_path / "users.json").exists()


# -------------------------------------------------------------------- amounts
@pytest.mark.parametrize(
    ("raw", "available", "expected"),
    [("50", 100, 50), ("1,000", 5000, 1000), ("all", 80, 80), ("MAX", 80, 80), (" 7 ", 10, 7)],
)
def test_parse_amount_ok(raw, available, expected):
    assert parse_amount(raw, available) == expected


@pytest.mark.parametrize(
    ("raw", "available"),
    [("0", 10), ("-5", 10), ("abc", 10), ("1.5", 10), ("11", 10), ("all", 0), ("١٢", 100)],
)
def test_parse_amount_rejects(raw, available):
    with pytest.raises(UserError):
        parse_amount(raw, available)


def test_parse_amount_all_override():
    assert parse_amount("all", 500, all_amount=120) == 120


def test_inventory_helpers():
    acc = {"inventory": []}
    inv_add(acc, "laptop", 2)
    inv_add(acc, "laptop", 1)
    assert inv_amount(acc, "laptop") == 3
    inv_add(acc, "laptop", -3)
    assert acc["inventory"] == []  # empty stacks vanish
    inv_add(acc, "laptop", -1)  # removing something you don't have adds nothing
    assert acc["inventory"] == []


def test_prices_and_fish():
    assert sell_price("laptop") == SHOP["laptop"].price // 2
    assert sell_price("rarefish") == FISH["rarefish"][1]
    assert sell_price("nonsense") is None
    random.seed(1)
    results = [roll_fish() for _ in range(2000)]
    assert results.count(None) > 400  # roughly a third are empty hooks
    assert {r[0] for r in results if r} == {"commonfish", "uncommonfish", "rarefish"}


# ------------------------------------------------------------------- counting
def fresh(**kw):
    return {"count": 1, "old_author": None, "saves": 0, **kw}


def test_counting_happy_path():
    s = fresh()
    assert process_count(s, 1, "1") == "ok"
    assert process_count(s, 2, " 2 ") == "ok"
    assert (s["count"], s["old_author"]) == (3, 2)


def test_counting_ignores_chatter():
    s = fresh()
    for text in ("hello", "1 2", "", "1.0", "-1", "١"):
        assert process_count(s, 1, text) == "ignore"
    assert s["count"] == 1


def test_counting_same_person_twice_ruins():
    s = fresh()
    process_count(s, 1, "1")
    assert process_count(s, 1, "2") == "ruined"
    assert (s["count"], s["old_author"]) == (1, None)


def test_counting_wrong_number_ruins():
    s = fresh()
    process_count(s, 1, "1")
    assert process_count(s, 2, "5") == "ruined"
    assert s["count"] == 1


def test_counting_save_absorbs_both_kinds_of_mistake():
    s = fresh(saves=2)
    process_count(s, 1, "1")
    assert process_count(s, 1, "2") == "saved"  # wrong turn
    assert process_count(s, 2, "9") == "saved"  # wrong number
    assert (s["count"], s["saves"], s["old_author"]) == (2, 0, 1)
    assert process_count(s, 2, "9") == "ruined"  # no saves left


# ---------------------------------------------------------------- tic-tac-toe
def test_find_winner():
    assert find_winner(["X", "X", "X", None, None, None, None, None, None]) == "X"
    assert find_winner(["O", None, None, None, "O", None, None, None, "O"]) == "O"
    assert find_winner(["X", "O", "X", "X", "O", "O", "O", "X", "X"]) is None
    assert find_winner([None] * 9) is None


def test_highlow_hint_never_equals_number():
    for _ in range(500):
        number, hint = make_highlow()
        assert 1 <= number <= 100 and 1 <= hint <= 100 and number != hint


# --------------------------------------------------------------------- automod
def test_banned_words_whole_word_and_wildcard():
    pattern = compile_banned_words(["cum", "fuck*", "anus", "blow job", "f u c k"])
    assert find_banned_word(pattern, "please read the document") is None  # "cum" inside a word
    assert find_banned_word(pattern, "Janus and Essex") is None
    assert find_banned_word(pattern, "oh CUM on") == "cum"
    assert find_banned_word(pattern, "what the fucking hell") == "fucking"
    assert find_banned_word(pattern, "a blow  job") is None  # exact phrase only
    assert find_banned_word(pattern, "a blow job!") == "blow job"
    assert find_banned_word(pattern, "f u c k") == "f u c k"
    assert find_banned_word(None, "anything") is None


def test_shipped_word_list_parses_and_compiles():
    from cogs.moderation import BANNED_WORDS_FILE

    words = parse_banned_words(BANNED_WORDS_FILE.read_text(encoding="utf-8"))
    assert len(words) > 50 and not any(w.startswith("#") for w in words)
    pattern = compile_banned_words(words)
    assert find_banned_word(pattern, "hello friends, how is everyone") is None
    assert find_banned_word(pattern, "you fucker") == "fucker"


# ---------------------------------------------------------------------- levels
def test_level_math():
    assert level_for(0) == 0
    assert level_for(49) == 0 and level_for(50) == 1
    assert level_for(199) == 1 and level_for(200) == 2
    assert level_for(5000) == 10
    for level in range(0, 30):
        assert level_for(xp_for_level(level)) == level


def test_progress_bar_shape():
    bar, into, needed = progress_bar(125)  # level 1: 50 -> 200
    assert (into, needed) == (75, 150)
    assert len(bar) == 20 and bar.count("🟦") == 10

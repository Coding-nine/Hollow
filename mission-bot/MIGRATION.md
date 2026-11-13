# Moving over from the old Replit version

## Keep your data

Copy these files from the old project into `data/` (the new code upgrades their format automatically on first start - e.g. empty `{}` inventories become lists and `"None"` strings become real nulls):

`users.json` · `serversettings.json` · `counting.json` · `countingscore.json` · `reports.json`

`missions.json`, `word.json` and `wordscore.json` are no longer used. Your MongoDB XP data keeps working as-is: put the connection string in `MONGO_URI` and the cog uses the same database (`discord`), collection (`levelling`) and `{id, xp}` documents. Levels are now computed with a simpler formula (level 1 at 50 XP, 2 at 200, 3 at 450 ...), so a few people's level numbers may shift slightly; nobody loses XP.

## Secrets

The old `main.py` had a full MongoDB connection string (with password) in a comment. Treat it as exposed: change that database user's password in MongoDB Atlas and put the new string only in `.env` / your host's secret settings. The same goes for the Discord token if it was ever pasted anywhere (Developer Portal -> Bot -> Reset Token). Environment variable is now `DISCORD_TOKEN` (was `TOKEN`).

## Files you can delete

`keep_alive.py` (Flask + UptimeRobot trick, not needed any more), `.replit` / `_replit`, `replit.nix`, `poetry.lock`, `pyproject.toml` / `pyproject_toml.backup`, `_breakpoints` - all Replit/Poetry leftovers.

## What changed

**Fixed bugs** (a selection): `ping` froze the whole bot for 2 seconds; the old `help` listed many commands that didn't exist; `$automod` crashed; `sbuy` had its "can you afford it" check backwards; tic-tac-toe used one global game shared by every server; `fish` never actually caught anything (its catch logic sat after a `break`); `sell` priced everything at 0 and never saved; the shop's RolexWatch couldn't be bought (leading space in its name); every `warn` after a user's first saved the reason "Swearing" instead of yours; the leaderboard crashed for users the bot couldn't see; coin flip / high-low waited forever for *anyone's* message; commands crashed in DMs; automod matched inside words (`cum` in "document").

**Modernised**: `discord.py` 2.x (async cogs, `display_avatar`, intents, buttons instead of `$place`), `discord_components` / `discord-py-slash-command` / `youtube_dl` / `praw` / `requests` / `Flask` dropped, one central error handler instead of ~20 copy-pasted ones, data held in memory and saved atomically instead of re-reading files on every call, `@everyone`/role pings blocked in bot replies.

**Rebuilt because they lived in the missing `cogs/` folder** (your help menu advertised them, but their code wasn't in `main.py`): `balance`, `deposit`, `withdraw`, `send`, `daily`, `beg`, `shop`, `kick`, `ban`, `mute`/`unmute` (now Discord timeouts), `clear`, `info`, `serverinfo`, welcome/leave messages, the level system.

**Removed**: pets (never worked), `passive` mode (nothing to protect you from), the word-chain game (fully commented out), `set ff` and `set muted` (nothing read them), the `se` / `channels` debug commands, the unused zenquotes helper.

**Not included** because the code wasn't in what was uploaded: memes/animals/jokes/8ball, music and lyrics, Brawl Stars, `question` / `wtd`. Send over your old `cogs/` folder and they can be ported.

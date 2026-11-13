# Hollow Bot

A multi-purpose Discord bot built with [discord.py](https://discordpy.readthedocs.io/) 2.x: an economy with a shop, games, a counting channel, moderation with a word-filter automod, per-server settings, and an optional MongoDB-backed level system.

## Features

| Category | Commands |
| --- | --- |
| **Economy** | `balance` `deposit` `withdraw` `send` `daily` `beg` `search` `postmeme` `fish` `shop` `buy` `sell` `inventory` `use` `leaderboard` `profile` |
| **Games** | `ttt @user` (Tic-Tac-Toe with buttons) `hl` (higher/lower) `cf <amount>` (coin flip) |
| **Counting** | counting game with saves, `counting` `countstats` `countboard` `serverbal` `servershop` `sbuy` |
| **Moderation** | `warn` `warns` `clearwarns` `kick` `ban` `mute` `unmute` `clear` `giverole` `removerole` `automod` |
| **Settings** (admins) | `set prefix` `set welcome` `set leave` `set counting` |
| **General** | `help` `ping` `info` `serverinfo` `botinfo` |
| **Levels** *(optional)* | `rank` `levels` - needs MongoDB, see below |

Use `$help` (the default prefix; change it per server with `set prefix`) to see everything - the help command builds itself from the loaded cogs.

## Setup

You need **Python 3.10 or newer**.

### 1. Create the bot on Discord

1. Go to the [Developer Portal](https://discord.com/developers/applications) -> **New Application** -> **Bot**.
2. Click **Reset Token** and copy it (you'll only see it once).
3. Under **Privileged Gateway Intents**, switch on **Message Content Intent** and **Server Members Intent**. The bot will refuse to log in without them.
4. Invite it with **OAuth2 -> URL Generator**: scope `bot`, plus the permissions you want it to use (Send Messages, Embed Links, Add Reactions, Read Message History, and for moderation: Manage Messages, Manage Roles, Kick Members, Ban Members, Moderate Members).

### 2. Run it

```bash
git clone <your-repo-url> mission-bot && cd mission-bot
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then put your token in .env
python bot.py
```

### Configuration

Settings come from environment variables; locally they're read from `.env` (never committed - it's in `.gitignore`). On a host, set them as environment variables/secrets instead.

| Variable | Required | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | Your bot token |
| `MONGO_URI` | no | MongoDB connection string. If empty, the level system is simply switched off |
| `MONGO_DB`, `MONGO_COLLECTION` | no | Default `discord` / `levelling` |
| `DATA_DIR` | no | Where the JSON data files live (default `./data`) |
| `PORT` | no | If set, serves a tiny health page on that port (see hosting) |

> **Keep secrets out of git.** Never write the token or the MongoDB string in code or comments. If either has ever been pasted somewhere public (or into a file you've shared), reset the token in the Developer Portal and change the database user's password in MongoDB Atlas.

## Keeping it online 24/7

A Discord bot connects *out* to Discord, so unlike the old Replit setup it **doesn't need a web server or an uptime pinger** - it just needs a machine that stays on and restarts it if it crashes. Free tiers and prices change often, so check each provider's current terms before committing. Typical choices:

- **A small VPS** (the simplest reliable option) - see the systemd unit below.
- **A machine you already own** that's always on (Raspberry Pi, old PC, home server).
- **A container platform** that can run a Dockerfile as a *worker/background service*. The included `Dockerfile` works anywhere Docker does. **Attach a persistent volume at `/data`**, otherwise your economy data is wiped on every redeploy.
- Some providers offer an always-free VM tier (with signup requirements and limited capacity).

Platforms that put idle *web services* to sleep are a poor fit even with a pinger. If a host insists on an HTTP port, set `PORT` and the bot serves `/` and `/health` (so UptimeRobot still works) - but prefer a real always-on worker.

### systemd (Linux VPS / Raspberry Pi)

`/etc/systemd/system/mission-bot.service`:

```ini
[Unit]
Description=Mission Bot
After=network-online.target
Wants=network-online.target

[Service]
User=bot
WorkingDirectory=/opt/mission-bot
EnvironmentFile=/opt/mission-bot/.env
ExecStart=/opt/mission-bot/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now mission-bot
journalctl -u mission-bot -f      # live logs
```

### Docker

```bash
docker build -t mission-bot .
docker run -d --name mission-bot --restart unless-stopped \
  --env-file .env -v mission-bot-data:/data mission-bot
```

## Data

Economy balances, warnings, server settings and counting state are stored as JSON files in `DATA_DIR` (default `data/`). They're loaded into memory at start-up, written atomically, and saved on shutdown. **They contain your users' data - they are git-ignored; back them up separately** (copy the folder now and then).

## Development

```bash
pip install -r requirements-dev.txt
ruff check .       # lint
pytest             # 50+ tests, no Discord connection needed
```

Layout:

```
bot.py            entry point (bot class, prefix lookup, cog loader)
cogs/             one file per feature area - every .py in here is auto-loaded
utils/            storage, shared helpers, optional health endpoint
banned_words.txt  the automod word list (edit freely)
tests/
```

To add a feature, create `cogs/my_feature.py` with a `Cog` and an `async def setup(bot)` - copy any existing cog as a template. Give each command a docstring: it becomes its `help` text automatically. Owners can hot-reload with `$reload my_feature`.

## Publishing to GitHub

```bash
git init -b main
git add .
git status          # check: no .env, nothing from data/ except .gitkeep
git commit -m "Initial commit"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Create the empty repository on github.com first. Don't reuse any old git history from before the secrets were cleaned up - start fresh as above. Add a `LICENSE` file if you want others to be able to reuse the code.

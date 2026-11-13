"""Optional tiny HTTP endpoint.

Discord bots connect *outwards*, so they don't need a web server. This only
exists for hosts that insist on a listening port / health check (some free
"web service" tiers) or if you still want an uptime monitor to ping the bot.
It is only started when the PORT (or HEALTH_PORT) environment variable is set.
Uses aiohttp, which discord.py already depends on - no Flask needed.
"""

from __future__ import annotations

from aiohttp import web


async def start_health_server(port: int) -> web.AppRunner:
    async def ok(_request: web.Request) -> web.Response:
        return web.Response(text="Mission Bot is up")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner

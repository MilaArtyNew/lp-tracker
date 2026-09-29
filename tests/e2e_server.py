"""Сервер для e2e-теста расширения: настоящий API + Postgres, on-chain чтение подменено данными из env.
Env: DATABASE_URL, BOT_TOKEN(любой), E2E_WALLET, E2E_POSITIONS (json-список), PORT.
Печатает одну JSON-строку {"code": ..., "url": ...} и работает до SIGTERM."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main():
    import asyncpg
    from api import server
    from db import pool, repo
    from modules import sync

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await conn.close()
    await pool.init()

    raw = json.loads(os.environ["E2E_POSITIONS"])
    fake = [SimpleNamespace(**{"token0_address": "0x0", "token1_address": "0x1", "fees0": 0, "fees1": 0,
                               "amount0": 1, "amount1": 1, "deposited0": 1, "deposited1": 1, **p}) for p in raw]

    async def fetch(_addr):
        return fake
    sync.fetch_all_chains_rpc = fetch

    uid = await repo.get_or_create_user(1)
    await repo.add_main_wallet(uid, os.environ["E2E_WALLET"], "main")
    await repo.add_main_wallet(uid, "0x" + "d4" * 20, "second")
    await sync.sync_user(uid)
    code = await repo.create_link_code(uid)

    port = int(os.getenv("PORT", "18080"))
    await server.start(port)
    print(json.dumps({"code": code, "url": f"http://127.0.0.1:{port}"}), flush=True)
    await asyncio.Event().wait()

asyncio.run(main())

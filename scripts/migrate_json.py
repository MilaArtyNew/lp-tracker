"""Разовая миграция: tracked-кошельки из users.json -> main-кошельки в общей базе.
Запуск (Railway shell): python scripts/migrate_json.py
wallets.json (зашифрованные ключи бота) не трогается — это отдельный этап (tech-кошелёк).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import pool, repo  # noqa: E402

USERS = os.path.join(os.getenv("USERDATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data")), "users.json")


async def main():
    await pool.init()
    with open(USERS) as f:
        data = json.load(f)
    for tg_id, rec in data.items():
        if rec.get("wallet"):
            uid = await repo.get_or_create_user(int(tg_id))
            w = await repo.add_main_wallet(uid, rec["wallet"])
            print(f"tg {tg_id}: {w['address']} active={w['is_active']}")

asyncio.run(main())

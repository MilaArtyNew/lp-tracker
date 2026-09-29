"""Автоперенос при старте: отслеживаемые кошельки из users.json -> main-кошельки общей базы.
Идемпотентно (повторный запуск ничего не дублирует), ошибки не роняют бота. Ключи (wallets.json) не трогаем."""
import json
import logging
import os

from db import repo

log = logging.getLogger(__name__)


def _users_path() -> str:
    base = os.getenv("USERDATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
    return os.path.join(base, "users.json")


async def import_users_json(path: str | None = None) -> int:
    path = path or _users_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        log.exception("users.json unreadable — skipping migration")
        return 0
    n = 0
    for tg_id, rec in data.items():
        addr = (rec or {}).get("wallet", "")
        if not (addr.startswith("0x") and len(addr) == 42 and str(tg_id).lstrip("-").isdigit()):
            continue
        try:
            uid = await repo.get_or_create_user(int(tg_id))
            await repo.add_main_wallet(uid, addr)
            n += 1
        except Exception:
            log.exception("migration failed for tg %s", tg_id)
    log.info("users.json -> db: %d wallets imported", n)
    return n

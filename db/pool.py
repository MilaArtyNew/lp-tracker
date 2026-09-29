"""Пул Postgres. Без DATABASE_URL бот работает по-старому (JSON), API не поднимается."""
import os

import asyncpg

_pool: asyncpg.Pool | None = None
_SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL"))


async def init() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    with open(_SCHEMA) as f:
        await _pool.execute(f.read())
    return _pool


def pool() -> asyncpg.Pool:
    assert _pool is not None, "db.pool.init() не вызван"
    return _pool

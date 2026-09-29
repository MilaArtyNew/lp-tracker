"""Воркер синхронизации: on-chain позиции активного main-кошелька -> общая база."""
import asyncio
import logging

from db import repo
from modules.rpc_tracker import fetch_all_chains_rpc

log = logging.getLogger(__name__)
SYNC_INTERVAL = 120


async def sync_wallet(user_id: int, wallet_id: int, address: str) -> int:
    positions = await fetch_all_chains_rpc(address)
    await repo.upsert_positions(user_id, wallet_id, positions)
    return len(positions)


async def sync_user(user_id: int) -> int | None:
    w = await repo.active_main(user_id)
    if not w:
        return None
    return await sync_wallet(user_id, w["id"], w["address"])


async def run_forever() -> None:
    while True:
        try:
            for w in await repo.all_active_mains():
                try:
                    await sync_wallet(w["user_id"], w["id"], w["address"])
                except Exception:
                    log.exception("sync failed for wallet %s", w["id"])
        except Exception:
            log.exception("sync loop error")
        await asyncio.sleep(SYNC_INTERVAL)

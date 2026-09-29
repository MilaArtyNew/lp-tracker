"""Воркер синхронизации: on-chain позиции активного main-кошелька -> общая база."""
import asyncio
import logging

from db import repo
from modules.rpc_tracker import fetch_all_chains_rpc

log = logging.getLogger(__name__)
SYNC_INTERVAL = 120

# async (user_id, events) -> None; задаётся ботом. Без него алерты просто не отправляются.
notify = None


def _range_events(prev: dict, positions: list) -> list[dict]:
    """Смена in_range у уже известных позиций. Новые позиции и первая синхронизация — без алерта."""
    events = []
    for p in positions:
        key = (p.chain, p.protocol, int(p.token_id))
        old, new = prev.get(key), p.status == "in_range"
        if old is None or old == new:
            continue
        events.append({"kind": "enter" if new else "exit", "position": p})
    return events


async def sync_wallet(user_id: int, wallet_id: int, address: str) -> int:
    prev = await repo.range_states(wallet_id)
    positions = await fetch_all_chains_rpc(address)
    await repo.upsert_positions(user_id, wallet_id, positions)
    if notify and prev:
        events = _range_events(prev, positions)
        if events:
            cfg = await repo.get_alert_settings(user_id)
            events = [e for e in events if cfg["range_enter" if e["kind"] == "enter" else "range_exit"]]
            if events:
                try:
                    await notify(user_id, events)
                except Exception:
                    log.exception("alert delivery failed for user %s", user_id)
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

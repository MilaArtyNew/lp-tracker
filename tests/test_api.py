"""Сквозной тест общей базы и API. Нужен Postgres: TEST_DATABASE_URL."""
import os
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL не задан")

A1 = "0x" + "a1" * 20
A2 = "0x" + "b2" * 20


def pos(token_id, status="in_range", value=100.0, fees=5.0, sym=("PEPE", "ETH")):
    return SimpleNamespace(
        token_id=str(token_id), chain="base", protocol="v4", token0_symbol=sym[0],
        token1_symbol=sym[1], token0_address="0x0", token1_address="0x1", status=status,
        price_lower=1.0, price_upper=2.0, current_price=1.5, amount0=1.0, amount1=2.0,
        fees0=0.1, fees1=0.2, deposited0=1.0, deposited1=2.0, value_usd=value, fees_usd=fees)


@pytest.fixture
async def env(monkeypatch):
    os.environ["DATABASE_URL"] = DSN
    import asyncpg
    from db import pool
    conn = await asyncpg.connect(DSN)   # чистая схема: тестовая база одноразовая
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await conn.close()
    await pool.init()
    from api import server
    from modules import sync
    fake = {A1: [pos(1), pos(2, "below")], A2: [pos(9)]}

    async def fetch(addr):
        return fake[addr]
    monkeypatch.setattr(sync, "fetch_all_chains_rpc", fetch)
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    yield client, fake
    await client.close()
    await pool.pool().close()


async def linked(client, tg_id=111):
    from db import repo
    uid = await repo.get_or_create_user(tg_id)
    code = await repo.create_link_code(uid)
    r = await client.post("/api/link", json={"code": code})
    assert r.status == 200
    return {"Authorization": "Bearer " + (await r.json())["token"]}, code


async def test_auth_required(env):
    client, _ = env
    assert (await client.get("/api/positions")).status == 401
    assert (await client.get("/api/positions", headers={"Authorization": "Bearer x"})).status == 401


async def test_link_code_is_single_use(env):
    client, _ = env
    _, code = await linked(client)
    assert (await client.post("/api/link", json={"code": code})).status == 400


async def test_wallet_switch_and_positions(env):
    client, fake = env
    h, _ = await linked(client)
    w1 = await (await client.post("/api/wallets", json={"address": A1}, headers=h)).json()
    w2 = await (await client.post("/api/wallets", json={"address": A2}, headers=h)).json()
    assert w1["is_active"] and not w2["is_active"]

    assert (await (await client.post("/api/positions/sync", headers=h)).json())["synced"] == 2
    ps = (await (await client.get("/api/positions?status=open", headers=h)).json())["positions"]
    assert {p["token_id"] for p in ps} == {1, 2} or {float(p["token_id"]) for p in ps} == {1.0, 2.0}
    assert sorted(p["in_range"] for p in ps) == [False, True]

    # смена кошелька: видим позиции второго, история первого не теряется
    assert (await client.post(f"/api/wallets/{w2['id']}/activate", headers=h)).status == 200
    ps = (await (await client.get("/api/positions", headers=h)).json())["positions"]
    assert len(ps) == 1 and float(ps[0]["token_id"]) == 9
    me = await (await client.get("/api/me", headers=h)).json()
    assert me["active_wallet"]["address"] == A2


async def test_position_closed_when_gone_onchain(env):
    client, fake = env
    h, _ = await linked(client)
    await client.post("/api/wallets", json={"address": A1}, headers=h)
    await client.post("/api/positions/sync", headers=h)
    fake[A1] = [pos(1)]            # позицию 2 закрыли где угодно
    await client.post("/api/positions/sync", headers=h)
    ps = (await (await client.get("/api/positions", headers=h)).json())["positions"]
    by = {int(p["token_id"]): p for p in ps}
    assert by[2]["status"] == "closed" and by[2]["closed_via"] == "external"
    assert by[1]["status"] == "open"


async def test_journal_sums_per_coin(env):
    client, _ = env
    h, _ = await linked(client)
    await client.post("/api/wallets", json={"address": A1}, headers=h)
    await client.post("/api/positions/sync", headers=h)
    ps = (await (await client.get("/api/positions", headers=h)).json())["positions"]
    for p, pnl in zip(ps, (10.5, -3.0)):
        r = await client.post(f"/api/positions/{p['id']}/journal", json={"note": "x", "pnl_usd": pnl}, headers=h)
        assert r.status == 201
    s = await (await client.get("/api/journal/summary", headers=h)).json()
    assert s[0]["symbol0"] == "PEPE" and s[0]["manual_pnl_usd"] == 7.5 and s[0]["positions"] == 2


async def test_users_are_isolated(env):
    client, _ = env
    h1, _ = await linked(client, 111)
    h2, _ = await linked(client, 222)
    await client.post("/api/wallets", json={"address": A1}, headers=h1)
    await client.post("/api/positions/sync", headers=h1)
    r = await (await client.get("/api/positions", headers=h2)).json()
    assert r["positions"] == []
    ps = (await (await client.get("/api/positions", headers=h1)).json())["positions"]
    assert (await client.post(f"/api/positions/{ps[0]['id']}/journal", json={"note": "x"}, headers=h2)).status == 404


async def test_range_alerts_only_on_change_and_respect_settings(env):
    from db import repo
    from modules import sync
    client, fake = env
    events = []

    async def notify(uid, evs):
        events.extend((e["kind"], int(e["position"].token_id)) for e in evs)
    sync.notify = notify
    try:
        h, _ = await linked(client)
        await client.post("/api/wallets", json={"address": A1}, headers=h)
        await client.post("/api/positions/sync", headers=h)
        assert events == []                                    # первая синхронизация — без алертов
        fake[A1] = [pos(1, "below"), pos(2, "below")]          # позиция 1 вышла из диапазона; 2 и так была вне
        await client.post("/api/positions/sync", headers=h)
        assert events == [("exit", 1)]
        fake[A1] = [pos(1, "below"), pos(2, "below")]
        await client.post("/api/positions/sync", headers=h)
        assert events == [("exit", 1)]                         # без изменений — тишина
        fake[A1] = [pos(1, "in_range"), pos(2, "below"), pos(3, "in_range")]   # 3 — новая, алерта нет
        await client.post("/api/positions/sync", headers=h)
        assert events == [("exit", 1), ("enter", 1)]
        uid = await repo.user_by_token(h["Authorization"][7:])
        await repo.set_alert(uid, "range_exit", False)
        fake[A1] = [pos(1, "above"), pos(2, "below"), pos(3, "in_range")]
        await client.post("/api/positions/sync", headers=h)
        assert events == [("exit", 1), ("enter", 1)]           # выход отключён в настройках
    finally:
        sync.notify = None


async def test_users_json_migration_is_idempotent_and_skips_garbage(env, tmp_path):
    import json
    from db import migrate, repo
    f = tmp_path / "users.json"
    f.write_text(json.dumps({"7": {"wallet": A1}, "8": {"wallet": "not-an-address"}, "x": {"wallet": A2}, "9": {}}))
    assert await migrate.import_users_json(str(f)) == 1          # только валидная запись
    assert await migrate.import_users_json(str(f)) == 1          # повтор ничего не дублирует
    uid = await repo.get_or_create_user(7)
    ws = await repo.list_wallets(uid)
    assert len(ws) == 1 and ws[0]["address"] == A1.lower() and ws[0]["is_active"]
    assert await migrate.import_users_json(str(tmp_path / "missing.json")) == 0

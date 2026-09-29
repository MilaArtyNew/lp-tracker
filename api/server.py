"""HTTP API для расширения. Авторизация: Bearer api-токен, выданный по коду из /link."""
import json
import os
from decimal import Decimal

from aiohttp import web

from db import repo
from modules import sync


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(type(o))


def _json(data, status=200) -> web.Response:
    return web.Response(text=json.dumps(data, default=_default), status=status,
                        content_type="application/json")


@web.middleware
async def cors(request, handler):
    origin = request.headers.get("Origin", "")
    allowed = origin.startswith("chrome-extension://")
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


PUBLIC = {"/health", "/api/link"}


@web.middleware
async def auth(request, handler):
    if request.method == "OPTIONS" or request.path in PUBLIC:
        return await handler(request)
    header = request.headers.get("Authorization", "")
    user_id = await repo.user_by_token(header[7:]) if header.startswith("Bearer ") else None
    if user_id is None:
        return _json({"error": "unauthorized"}, 401)
    request["user_id"] = user_id
    return await handler(request)


async def health(_):
    return _json({"ok": True})


async def link(request):
    body = await request.json()
    token = await repo.redeem_link_code(str(body.get("code", "")), str(body.get("label", "extension")))
    if not token:
        return _json({"error": "invalid or expired code"}, 400)
    return _json({"token": token})


async def me(request):
    uid = request["user_id"]
    return _json({"active_wallet": await repo.active_main(uid),
                  "wallets": await repo.list_wallets(uid)})


async def wallets_add(request):
    body = await request.json()
    addr = str(body.get("address", "")).strip()
    if not (addr.startswith("0x") and len(addr) == 42):
        return _json({"error": "invalid address"}, 400)
    w = await repo.add_main_wallet(request["user_id"], addr, str(body.get("label", "")))
    return _json(w, 201)


async def wallets_activate(request):
    ok = await repo.activate_wallet(request["user_id"], int(request.match_info["id"]))
    if not ok:
        return _json({"error": "wallet not found"}, 404)
    await sync.sync_user(request["user_id"])
    return _json({"ok": True})


async def positions(request):
    uid = request["user_id"]
    w = await repo.active_main(uid)
    status = request.query.get("status")
    rows = await repo.list_positions(uid, w["id"] if w else None, status) if w else []
    return _json({"wallet": w, "positions": rows})


async def positions_sync(request):
    n = await sync.sync_user(request["user_id"])
    if n is None:
        return _json({"error": "no active wallet"}, 400)
    return _json({"synced": n})


async def journal_add(request):
    body = await request.json()
    pnl = body.get("pnl_usd")
    ok = await repo.add_note(request["user_id"], int(request.match_info["id"]),
                             body.get("note"), float(pnl) if pnl is not None else None)
    return _json({"ok": True}, 201) if ok else _json({"error": "position not found"}, 404)


async def journal_summary(request):
    return _json(await repo.journal_summary(request["user_id"]))


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors, auth])
    app.add_routes([
        web.get("/health", health),
        web.post("/api/link", link),
        web.get("/api/me", me),
        web.post("/api/wallets", wallets_add),
        web.post("/api/wallets/{id}/activate", wallets_activate),
        web.get("/api/positions", positions),
        web.post("/api/positions/sync", positions_sync),
        web.post("/api/positions/{id}/journal", journal_add),
        web.get("/api/journal/summary", journal_summary),
        web.route("OPTIONS", "/{tail:.*}", health),
    ])
    return app


async def start(port: int | None = None) -> web.AppRunner:
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port or int(os.getenv("PORT", "8080"))).start()
    return runner

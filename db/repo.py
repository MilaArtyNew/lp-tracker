"""Запросы к общей базе. Используют и бот, и API."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from db.pool import pool

LINK_TTL = timedelta(minutes=10)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_or_create_user(tg_id: int) -> int:
    row = await pool().fetchrow(
        """INSERT INTO users (tg_id) VALUES ($1)
           ON CONFLICT (tg_id) DO UPDATE SET tg_id = EXCLUDED.tg_id
           RETURNING id""", tg_id)
    return row["id"]


# ---- привязка расширения ----------------------------------------------------

async def create_link_code(user_id: int) -> str:
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    await pool().execute(
        "INSERT INTO link_codes (code, user_id, expires_at) VALUES ($1, $2, $3)",
        code, user_id, datetime.now(timezone.utc) + LINK_TTL)
    return code


async def redeem_link_code(code: str, label: str = "extension") -> str | None:
    """Меняет одноразовый код на api-токен. Код гасится атомарно."""
    async with pool().acquire() as c, c.transaction():
        row = await c.fetchrow(
            """UPDATE link_codes SET used_at = now()
               WHERE code = $1 AND used_at IS NULL AND expires_at > now()
               RETURNING user_id""", code.strip().upper())
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        await c.execute(
            "INSERT INTO api_tokens (token_hash, user_id, label) VALUES ($1, $2, $3)",
            _hash(token), row["user_id"], label)
        return token


async def user_by_token(token: str) -> int | None:
    row = await pool().fetchrow(
        "SELECT user_id FROM api_tokens WHERE token_hash = $1 AND revoked_at IS NULL",
        _hash(token))
    return row["user_id"] if row else None


# ---- кошельки ---------------------------------------------------------------

async def list_wallets(user_id: int, kind: str = "main") -> list[dict]:
    rows = await pool().fetch(
        """SELECT id, kind, address, label, is_active FROM wallets
           WHERE user_id = $1 AND kind = $2 ORDER BY id""", user_id, kind)
    return [dict(r) for r in rows]


async def add_main_wallet(user_id: int, address: str, label: str = "") -> dict:
    """Добавляет main-кошелёк; если активного нет — делает его активным."""
    address = address.lower()
    async with pool().acquire() as c, c.transaction():
        has_active = await c.fetchval(
            "SELECT 1 FROM wallets WHERE user_id=$1 AND kind='main' AND is_active", user_id)
        row = await c.fetchrow(
            """INSERT INTO wallets (user_id, kind, address, label, is_active)
               VALUES ($1, 'main', $2, $3, $4)
               ON CONFLICT (user_id, kind, address)
               DO UPDATE SET label = COALESCE(NULLIF(EXCLUDED.label, ''), wallets.label)
               RETURNING id, kind, address, label, is_active""",
            user_id, address, label or f"{address[:6]}…{address[-4:]}", not has_active)
        return dict(row)


async def activate_wallet(user_id: int, wallet_id: int) -> bool:
    async with pool().acquire() as c, c.transaction():
        ok = await c.fetchval(
            "SELECT 1 FROM wallets WHERE id=$1 AND user_id=$2 AND kind='main'",
            wallet_id, user_id)
        if not ok:
            return False
        await c.execute(
            "UPDATE wallets SET is_active=false WHERE user_id=$1 AND kind='main' AND is_active",
            user_id)
        await c.execute("UPDATE wallets SET is_active=true WHERE id=$1", wallet_id)
        # Права техкошелька были выданы на старый адрес — отзываем, выдать заново.
        await c.execute(
            "UPDATE delegations SET revoked_at = now() WHERE main_id <> $1 AND revoked_at IS NULL "
            "AND main_id IN (SELECT id FROM wallets WHERE user_id=$2)", wallet_id, user_id)
        return True


async def active_main(user_id: int) -> dict | None:
    row = await pool().fetchrow(
        """SELECT id, address, label FROM wallets
           WHERE user_id=$1 AND kind='main' AND is_active""", user_id)
    return dict(row) if row else None


async def all_active_mains() -> list[dict]:
    rows = await pool().fetch(
        "SELECT id, user_id, address FROM wallets WHERE kind='main' AND is_active")
    return [dict(r) for r in rows]


# ---- позиции ----------------------------------------------------------------

_POS_COLS = ("pool_id", "token0", "token1", "symbol0", "symbol1", "fee_ppm")


async def upsert_positions(user_id: int, wallet_id: int, positions: list) -> None:
    """Сохраняет снимок позиций кошелька; пропавшие on-chain помечает closed."""
    seen: set[tuple[str, str, int]] = set()
    async with pool().acquire() as c, c.transaction():
        for p in positions:
            tid = int(p.token_id)
            seen.add((p.chain, p.protocol, tid))
            await c.execute(
                """INSERT INTO positions
                   (user_id, wallet_id, chain, protocol, token_id, token0, token1, symbol0, symbol1,
                    status, in_range, price_lower, price_upper, current_price, amount0, amount1,
                    fees0, fees1, deposited0, deposited1, value_usd, fees_usd, opened_via, synced_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open',$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,
                           'external', now())
                   ON CONFLICT (chain, protocol, token_id) DO UPDATE SET
                     wallet_id=EXCLUDED.wallet_id, status='open', closed_at=NULL, closed_via=NULL,
                     in_range=EXCLUDED.in_range, price_lower=EXCLUDED.price_lower,
                     price_upper=EXCLUDED.price_upper, current_price=EXCLUDED.current_price,
                     amount0=EXCLUDED.amount0, amount1=EXCLUDED.amount1, fees0=EXCLUDED.fees0,
                     fees1=EXCLUDED.fees1, deposited0=EXCLUDED.deposited0,
                     deposited1=EXCLUDED.deposited1, value_usd=EXCLUDED.value_usd,
                     fees_usd=EXCLUDED.fees_usd, synced_at=now()""",
                user_id, wallet_id, p.chain, p.protocol, tid,
                p.token0_address, p.token1_address, p.token0_symbol, p.token1_symbol,
                p.status == "in_range",
                p.price_lower, p.price_upper, p.current_price, p.amount0, p.amount1,
                p.fees0, p.fees1, p.deposited0, p.deposited1, p.value_usd, p.fees_usd)
        opens = await c.fetch(
            "SELECT id, chain, protocol, token_id FROM positions WHERE wallet_id=$1 AND status='open'",
            wallet_id)
        gone = [r["id"] for r in opens if (r["chain"], r["protocol"], int(r["token_id"])) not in seen]
        if gone:
            await c.execute(
                "UPDATE positions SET status='closed', closed_at=now(), closed_via=COALESCE(closed_via,'external'), "
                "synced_at=now() WHERE id = ANY($1::bigint[])", gone)


async def list_positions(user_id: int, wallet_id: int | None, status: str | None) -> list[dict]:
    rows = await pool().fetch(
        """SELECT * FROM positions
           WHERE user_id=$1 AND ($2::bigint IS NULL OR wallet_id=$2) AND ($3::text IS NULL OR status=$3)
           ORDER BY status, chain, symbol0, token_id""", user_id, wallet_id, status)
    return [dict(r) for r in rows]


# ---- журнал -----------------------------------------------------------------

async def add_note(user_id: int, position_id: int, note: str | None, pnl_usd: float | None) -> bool:
    ok = await pool().fetchval("SELECT 1 FROM positions WHERE id=$1 AND user_id=$2", position_id, user_id)
    if not ok:
        return False
    await pool().execute(
        "INSERT INTO journal_notes (position_id, note, pnl_usd) VALUES ($1,$2,$3)",
        position_id, note, pnl_usd)
    return True


async def journal_summary(user_id: int) -> list[dict]:
    """Сумма ручного PnL и комиссий по монете (пара symbol0/symbol1)."""
    rows = await pool().fetch(
        """SELECT p.symbol0, p.symbol1,
                  count(*) AS positions,
                  COALESCE(sum(j.manual), 0) AS manual_pnl_usd,
                  COALESCE(sum(p.fees_usd), 0) AS fees_usd
           FROM positions p
           LEFT JOIN (SELECT position_id, sum(pnl_usd) AS manual FROM journal_notes GROUP BY position_id) j
                  ON j.position_id = p.id
           WHERE p.user_id = $1 GROUP BY p.symbol0, p.symbol1 ORDER BY manual_pnl_usd DESC""", user_id)
    return [dict(r) for r in rows]

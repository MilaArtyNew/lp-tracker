-- Общая база lp-tracker: используют и Telegram-бот, и расширение (через API).
-- Кошельков может быть несколько, активный один (is_active) — его можно сменить.
-- Сети — поле chain у позиции.

CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Одноразовый код: бот /link -> пользователь вводит в расширении -> api_token
CREATE TABLE IF NOT EXISTS link_codes (
    code        TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash  TEXT PRIMARY KEY,             -- sha256, сам токен не хранится
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

-- kind=main: только адрес (подписывает Rabby), ключа нет.
-- kind=tech: технический кошелёк бота, ключ зашифрован мастер-ключом из env.
CREATE TABLE IF NOT EXISTS wallets (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('main', 'tech')),
    address        TEXT NOT NULL,
    label          TEXT,
    encrypted_key  TEXT,
    is_active      BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, address),
    CHECK (kind = 'tech' OR encrypted_key IS NULL)
);
-- Ровно один активный main и один активный tech на пользователя
CREATE UNIQUE INDEX IF NOT EXISTS one_active_wallet ON wallets(user_id, kind) WHERE is_active;

-- Ключ позиции — (chain, protocol, token_id): NFT on-chain, не запись бота.
CREATE TABLE IF NOT EXISTS positions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wallet_id     BIGINT NOT NULL REFERENCES wallets(id),  -- владелец NFT; история не теряется при смене кошелька
    chain         TEXT NOT NULL,              -- base | ethereum | arbitrum | bnb | robinhood
    protocol      TEXT NOT NULL,              -- v3 | v4
    token_id      NUMERIC(78,0) NOT NULL,
    pool_id       TEXT,
    token0        TEXT,
    token1        TEXT,
    symbol0       TEXT,
    symbol1       TEXT,
    fee_ppm       INTEGER,
    tick_lower    INTEGER,
    tick_upper    INTEGER,
    liquidity     NUMERIC(78,0),
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    in_range      BOOLEAN,
    price_lower   NUMERIC,
    price_upper   NUMERIC,
    current_price NUMERIC,
    amount0       NUMERIC,
    amount1       NUMERIC,
    fees0         NUMERIC,
    fees1         NUMERIC,
    deposited0    NUMERIC,
    deposited1    NUMERIC,
    value_usd     NUMERIC,
    fees_usd      NUMERIC,
    opened_via    TEXT CHECK (opened_via IN ('bot', 'extension', 'external')),
    closed_via    TEXT CHECK (closed_via IN ('bot', 'extension', 'external')),
    opened_at     TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, protocol, token_id)
);
CREATE INDEX IF NOT EXISTS positions_wallet_status ON positions(wallet_id, status);

-- Для PnL/ROI: внесено, получено, собранные комиссии
CREATE TABLE IF NOT EXISTS position_events (
    id           BIGSERIAL PRIMARY KEY,
    position_id  BIGINT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('deposit', 'withdraw', 'collect')),
    amount0      NUMERIC,
    amount1      NUMERIC,
    usd          NUMERIC,
    tx_hash      TEXT,
    block_time   TIMESTAMPTZ,
    UNIQUE (tx_hash, position_id, kind)
);

-- Журнал: заметки по позе, сумма по монете считается в запросе
CREATE TABLE IF NOT EXISTS journal_notes (
    id           BIGSERIAL PRIMARY KEY,
    position_id  BIGINT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    note         TEXT,
    pnl_usd      NUMERIC,                    -- ручной результат (продажа на GMGN и т.п.)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS grids (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chain       TEXT NOT NULL,
    pool_id     TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('standard', 'minimize_second', 'single_sided')),
    params      JSONB NOT NULL,              -- диапазоны, веса, капитал
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_settings (
    user_id          BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    range_enter      BOOLEAN NOT NULL DEFAULT true,
    range_exit       BOOLEAN NOT NULL DEFAULT true,
    volume_spike_pct NUMERIC,
    volume_drop_pct  NUMERIC
);

-- Делегирование техническому кошельку привязано к паре (main, tech, chain):
-- при смене main права выдаются заново, старые отзываются (revoked_at).
CREATE TABLE IF NOT EXISTS delegations (
    id           BIGSERIAL PRIMARY KEY,
    main_id      BIGINT NOT NULL REFERENCES wallets(id),
    tech_id      BIGINT NOT NULL REFERENCES wallets(id),
    chain        TEXT NOT NULL,
    contract     TEXT NOT NULL,
    granted_tx   TEXT,
    revoked_at   TIMESTAMPTZ,
    UNIQUE (main_id, tech_id, chain, contract)
);

-- Команды технического кошелька: API кладёт, воркер бота исполняет.
-- Атомарный claim: UPDATE ... SET status='running' WHERE status='queued'.
CREATE TABLE IF NOT EXISTS tech_commands (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position_id  BIGINT REFERENCES positions(id),
    action       TEXT NOT NULL CHECK (action IN ('close', 'collect')),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed')),
    tx_hash      TEXT,
    error        TEXT,
    source       TEXT NOT NULL CHECK (source IN ('bot', 'extension')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

# LP Tracker

Telegram bot for tracking DeFi liquidity positions and building LP range ladders.

The bot is designed for lightweight Uniswap-style LP monitoring across multiple EVM chains. It can manage tracked wallets, show open positions, generate LP reports, and help build laddered range strategies.

## Features

- Telegram bot command menu.
- Wallet tracking for LP positions.
- Open position reports.
- LP range ladder generation.
- Multi-chain RPC configuration with optional Alchemy key.
- Railway deployment files.

## Supported Chains

Configured chains include:

- Ethereum
- Arbitrum
- Base
- BNB Chain

RPC URLs use public endpoints by default. If `ALCHEMY_API_KEY` is set, Alchemy RPC URLs are used where available.

## Commands

- `/new_ladder` — build an LP range ladder.
- `/wallets` — manage tracked wallets.
- `/track` — add a wallet to tracking.
- `/report` — show a full report for all LP positions.
- `/strategies` — list positions by wallet.
- `/help` — show help.

## Environment

```bash
cp .env.example .env
```

Required:

- `BOT_TOKEN` — Telegram bot token from BotFather.

Optional:

- `ALCHEMY_API_KEY` — improves RPC reliability and archive-query support.

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## Deployment

The repository includes Railway files:

- `Procfile`
- `railway.toml`
- `runtime.txt`

Set environment variables in Railway before deployment.

## Security

- Do not commit `.env`.
- Keep Telegram and RPC keys in environment variables only.

# Lp Tracker

LP tracker bot for monitoring liquidity-provider strategies, wallets, ladders, and reports. It helps track DeFi LP positions and strategy ladders through Telegram commands.

## Features

- Tracks LP wallets and strategy ladders.
- Provides commands for reports, wallets, strategies, and new ladder setup.
- Documents environment variables and operational deployment notes.

## Architecture

- **Repository:** `MilaArtyNew/lp-tracker`
- **Primary stack:** Python, Docker, Railway
- **Entrypoints and scripts:**
  - `bot.py`
- **Notable dependencies:** `aiogram`, `aiohttp`, `cryptography`, `eth-account`, `python-dotenv`, `web3`

## Configuration

Configure the service with environment variables. Do not commit real secrets to the repository.

- `ALCHEMY_API_KEY` — required or optional runtime configuration. See deployment environment for the actual value.
- `BOT_TOKEN` — required or optional runtime configuration. See deployment environment for the actual value.
- `THEGRAPH_API_KEY` — required or optional runtime configuration. See deployment environment for the actual value.
- `USERDATA_DIR` — required or optional runtime configuration. See deployment environment for the actual value.

## Setup

```bash
git clone https://github.com/MilaArtyNew/lp-tracker
cd lp-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running Locally

```bash
python bot.py
```

## Bot Commands

- `/help` — Show help and available commands.
- `/new_ladder` — Create a new LP ladder.
- `/report` — Generate or send a report.
- `/start` — Start the bot and show the main entry message.
- `/strategies` — List configured strategies.
- `/track` — Track a new LP/wallet/position.
- `/wallets` — List configured wallets.

If a command requires extra input and the argument is missing, the bot should ask a follow-up question instead of failing silently.

## Deployment Notes

- Keep secrets in the deployment platform environment variables, not in Git.
- Use the default branch as the source of truth for deployments.
- Check logs after every deployment and verify the `/status` or health endpoint when available.
- If the project uses a scheduler, verify timezone assumptions and idempotency before enabling it in production.

## Operational Notes

- Review logs after startup for missing environment variables or API authentication errors.
- Keep command names in English and document every user-facing command in this README.
- For Telegram bots, `/help` should list the same commands documented here.
- Inline buttons should edit the original message with the final status rather than sending duplicate messages.

## Troubleshooting

- **Bot does not respond:** verify the bot token, webhook/polling mode, and chat permissions.
- **Missing data:** check API keys, rate limits, and upstream service status.
- **Deployment starts but exits:** inspect platform logs for missing environment variables or import errors.
- **Commands differ from README:** update the command list here and in the bot command menu at the same time.

## Security

- Never commit `.env` files, API keys, private keys, Telegram tokens, or session strings.
- Use `.env.example` for placeholders only.
- Rotate any credential that was accidentally committed.

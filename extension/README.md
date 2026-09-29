# LP Tracker — Chrome extension

Uniswap v3/v4 LP positions, one-click **Close / Collect fees** via your wallet (Rabby/MetaMask), **LP Grid** with preview, PnL journal.
Shares data with the Telegram bot `@lp_mi_bot` through one API + Postgres.

## Install (no build needed)
1. `chrome://extensions` → enable *Developer mode* → **Load unpacked** → pick `extension/dist`.
2. In the bot send `/link`, copy the code.
3. Extension → *Settings* → enter the API URL (Railway service domain) and the code → **Link**.
4. Positions appear within ~2 minutes (the server syncs every 120 s). Click **↻** to sync now.

To sign transactions keep any normal web page open in the active tab with Rabby enabled (e.g. gmgn.ai) and open the extension from there.
The extension talks to the wallet through that tab; it never sees a private key.

## Features
| Tab | What it does |
|---|---|
| Positions | Active wallet's positions, range bar, value/fees, badge on the icon = number of out-of-range positions. **Collect fees** and **Close** for Uniswap v4 (v3: use the DEX site). Journal note + manual result per position. |
| Grid | Find a v4 pool by Pool ID or token/symbol (DexScreener), verified on-chain via PositionManager/StateView. Modes Standard / Minimize second token / Single-sided, bids or asks, weights, preview, then **one transaction** for all ranges. Approvals go through Permit2 for exact amounts, 1 hour. |
| Journal | Result per coin (sum of manual results) and fees. |
| Settings | API URL, link code, wallet list — switch the active wallet, add a wallet address. |

Every transaction: reads the position on-chain → builds calldata → **simulates it (eth_call)** → shows a confirmation with the simulation result → only then asks the wallet. The wallet account must equal the active wallet, otherwise nothing is sent. Funds always go to the active wallet.

## Mode definitions (own definitions — Liquidity Ladder publishes none)
- **Standard** — range edges at the nearest tick; the nearest range may straddle the price and then needs both tokens.
- **Minimize second token** — the nearest range sticks out past the price by at most one tick spacing.
- **Single-sided** — all ranges strictly on one side of the price; the second token is exactly zero.

## Develop
```
npm i
npm test                 # unit tests (offline): tick math, grid invariants, calldata decode, RPC failover
npm run simulate         # calldata vs the REAL PositionManager on Base via eth_call (network)
npm run build            # src -> dist (dist is committed)
# e2e: real API + Postgres + this extension in Chromium + mock wallet
DATABASE_URL=postgresql://postgres:test@127.0.0.1:55433/postgres PYTHON=/path/to/venv/python node test/e2e.mjs
```
`src/chains.json` is the chain config (PositionManager, StateView, RPC list); a test checks it against the addresses in the Python bot.

## Not done / limits
- **Robinhood Chain**: no RPC / contract addresses known — not configured. Add an entry to `src/chains.json`, the manifest host permission and `modules/rpc_tracker.py`.
- Closing v3 positions, PnL/ROI from the full deposit/withdraw history, range charts in USD/MC, volume spike alerts.
- Grid **creation** was verified by simulation only for a native-token pool (single-sided ETH, balance override). The ERC20 path (Permit2 approvals + settle) is not verified with real funds.
- Closing from Telegram without a wallet confirmation needs the delegate contract — not built.

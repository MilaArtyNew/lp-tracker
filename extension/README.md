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
| Positions | Active wallet's positions, range bar with a **unit switch: price / USD / ETH / market cap** (pools `TOKEN/ETH`, `TOKEN/USDT`, `TOKEN/USDG` in any token order: the pair is oriented so you always see the *asset's* price in the quote), value/fees, badge on the icon = number of out-of-range positions. **Collect fees** and **Close** for Uniswap v4 (v3: use the DEX site). Journal note + manual result per position. |
| Grid | Find a v4 pool by Pool ID or token/symbol (DexScreener), verified on-chain via PositionManager/StateView. Works in asset terms: **Bids** = buy the token below the price, deposit ETH/USDT; **Asks** = sell the token above the price, deposit the token; % are relative to the *asset's* price whatever the token order in the pool. Modes Standard / Minimize second token / Single-sided, weights, preview, then **one transaction** for all ranges. Approvals go through Permit2 for exact amounts, 1 hour. |
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

## Pair orientation (why it matters)
Uniswap orders tokens by **address**, not by meaning; in v4 native ETH is always `currency0`. So in a `TOKEN/ETH` pool the pool price is *tokens per ETH* — the inverse of the token's price — and «below the price» means the opposite side. The extension (`core/orient.js`) and the bot ladder (`modules/ladder_plan.py`) work in **asset/quote** terms and convert to pool terms.
Verified on real pools with `eth_call` (state overrides for balance, allowance and Permit2): native ETH as `currency0` (Robinhood CRCL/ETH), USDC as `currency1` (Base tBTC/USDC) and as `currency0` (Base Basecat/USDC), USDT (Base USDbC/USDT), USDG (Robinhood AMC/USDG). Pools with hooks are built from their key but the hook's behaviour is unknown — the simulation before signing shows whether it accepts the position. Pools created outside PositionManager (many Clanker/Zora-style pools) have no key in `poolKeys`; they cannot be built from a Pool ID.

## Networks
Base, Ethereum, Arbitrum, BNB and **Robinhood Chain** (chain id 4663, `https://rpc.mainnet.chain.robinhood.com`). Robinhood Chain addresses come from the Uniswap deployment docs and were cross-checked on-chain (PositionManager and StateView point to the same PoolManager, NFPM to the factory, WETH matches DexScreener). Uniswap **v3 and v4** both exist there; the bot reads both.
USD prices: CoinGecko by contract; Robinhood Chain and native tokens via DexScreener / the wrapped token.

## Not done / limits
- Closing v3 positions, PnL/ROI from the full deposit/withdraw history, range charts by market cap, volume spike alerts.
- Grid **creation** is verified by `eth_call` simulation on real pools for native ETH and for USDC deposits (through Permit2); it has not been sent with real funds, and a token whose storage layout I could not override (e.g. CRCL) was not simulated.
- Robinhood Chain: the public RPC keeps state only for the last ~100k blocks (checked: head-1000 works, head-100000 fails), so the "deposited amount" of older positions cannot be reconstructed there — PnL from deposits is unavailable for that chain.
- Closing from Telegram without a wallet confirmation needs the delegate contract — not built.

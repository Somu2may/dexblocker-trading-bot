# DexBlocker — paper memecoin scanner (default) + BULK futures

**PAPER TRADING ONLY.** No wallet, no private keys, no signing, no real orders. `mode: paper` is hard-locked in code
(`futbot/config.py`, `futbot/execution.make`) and cannot be changed from Telegram or the Mini App.

Default strategy is **`gmgn_meme`** (Solana memecoins). Futures stay selectable and are not removed:
`/set strategy.name hf_maker` (also `donchian_15m`, `donchian_1h`, `scalper`). The live VM config is not overwritten
just because this repo default changed — see `docs/GMGN_MIGRATION.md`.

`gmgn_meme` reads GMGN's read-only HTTP API when `GMGN_API_KEY` is set (never the public demo key, never swap or
signing routes). If that key is missing, the bot logs **no-key mode** and paper-trades from free DexScreener
(boosts, new profiles, trending metas) plus RugCheck only. GMGN-only gates are skipped; every other gate still
rejects when its measurement is missing. A price quote does not prove the token can be sold. Details are in
`docs/GMGN_MIGRATION.md`.

```
python bot/dry_run_meme.py --limit 8     # public DexScreener scan, no Telegram
```

## Futures (still in this tree)

Venue model: **BULK** (bulk.trade, Solana perp DEX) — BTC-USD, ETH-USD, SOL-USD perps, long + short, max **3x**.
Symbols are configurable (`symbols` / `enabled_symbols` in config.yaml); any symbol whose BULK `exchangeInfo` status is not
`TRADING` is skipped automatically (BNB-USD is SUSPENDED today).

```
bot/futbot/      engine (asyncio), feeds (BULK + fallbacks), strategy, indicators, portfolio (paper book), execution, store, tg
bot/backtest.py  downloads 60d of 1m data and prints the walk-forward / BULK-candle / fee-comparison report (docs/BACKTEST.md)
bot/dry_run.py   runs the engine against live data WITHOUT Telegram (alerts printed)
bot/config.yaml  every parameter (strategy, exits, risk, fees, data sources)
miniapp_app/     FastAPI API + static Mini App (Telegram initData HMAC auth, only TELEGRAM_CHAT_ID or ALLOWED_USER_ID)
launch_bot.py    engine + Telegram bot + Mini App hooks; start.sh = supervisor with heartbeat watchdog
scripts/         gcp_install.sh (VM installer), migrate_futures.py (archive old memecoin data), probe_sources.py
```

## Market data
| role | source |
|---|---|
| prices (primary) | BULK mainnet REST `https://mainnet-api1.bulk.trade/api/v1` (klines, ticker, l2book, exchangeInfo) + WS `wss://mainnet-ws1.bulk.trade` (ticker: last + mark) |
| fallback | `https://exchange-api.bulk.trade/api/v1` = BULK **testnet** per docs → used only for exchangeInfo and the oracle price (flagged "proxy") |
| proxy prices if BULK is down | Hyperliquid, then OKX (entries are blocked while on a proxy; exits keep running) |
| volume filter | reference venue volume (OKX → Bybit → Binance → Hyperliquid); BULK volume is too thin/zero on many 1m bars |
| funding | BULK ticker `fundingRate`, hourly |

## Active strategy: ⚠️ EXPERIMENT `hf_maker` - 1m maker scalper (`strategy.name: hf_maker`, default since 2026-11-02 late)
Owner asked for >= ~50 trades/day. On every closed 1m bar (per coin, when flat) the bot rests post-only maker quotes at
EMA60 - 3 x ATR(1m) (buy) and EMA60 + 3 x ATR (sell), passive side only, valid for one minute. A quote fills only when the BULK
mark trades THROUGH it by 1 bp (paper fill at the limit, maker fee 0 bps). Exit: maker target at the EMA60 of the signal bar
(trade-through), taker stop 8 ATR, taker time exit after 120 min. Risk 0.25 %/trade, max 3 open (one per coin), <= 3x,
3 % daily loss cap (rest of IST day), 4-loss streak -> 30-min pause (`strategy.hf.risk.*`). Per-trade Telegram messages are off
for hf (50+/day; `strategy.hf.notify_trades`); the 2-day auto report shows trades/day vs the 50 target and daily-cap days.
**Backtest (docs/BACKTEST_HF.md): Binance 1m 245 d -> 60.3 trades/day, win 65.3 %, PF 0.76, net -91.8 %, max DD 91.9 %;
BULK 60 d -> 52.5/day, PF 0.61, net -63.9 %. No edge even with zero fees (PF 0.94).** Paper only.
Switch: `/set strategy.name donchian_15m` / `donchian_1h`.

Mini App **Chart** tab: TradingView Lightweight Charts (bundled `static/lightweight-charts.standalone.production.js`, v4.2.3,
Apache-2.0), BTC/ETH/SOL x 1m/5m/15m BULK klines via `/api/candles` (cached 5 s), forming bar driven by the bot's live BULK mark,
3 s polling; markers for bot entries (▲ long / ▼ short) and exits (● P&L), price lines for open-position entry/stop/target and
the resting maker bid/ask.

## Previous strategy: 🧪 EXPERIMENTAL Donchian-20 15m breakout (`strategy.name: donchian_15m`, selectable)
Owner asked for a faster timeframe. Same rules as the 1h version one step down (`strategy.donchian_15m`):
- Signal on each closed 15m bar: close > highest high of the prior 20 x 15m bars AND last closed 1h close > 1h EMA50.
  Long-only by default (`long_only: false` adds the mirror short; backtest: worse). Stop 3 x ATR(14, 15m), chandelier trail
  3 ATR ratcheted on each closed 15m bar, no TP, no max hold. Same risk rules and breakers.
- Warm-up: 600 x 15m + 300 x 1h closed klines (BULK, fallback OKX/Hyperliquid/Binance/Bybit) -> ready on the next 15m close.
- **Backtest (bot/research/donchian15.py, 700 d, BULK fees): PF 0.74, win 30.1 %, 18.2 trades/week, avg R -0.108,
  net -85.8 %, max DD 85.8 %, every 90-day window negative. Even with zero fees PF is 0.97.** It is a paper experiment only;
  `/report` (and the automatic report) compare live results with these numbers. Switch back: `/set strategy.name donchian_1h`.

## Automatic report
Every `report.every_days` days (default 2, counted from `report.anchor_date`) at `report.at_ist` (default 20:00 IST) the bot
sends the `/report` content plus open positions to the Telegram chat. `/set report.every_days 0` turns it off.
A restart later the same evening still sends that day's report once; missed days are not sent later.

## Previous strategy: 🧪 EXPERIMENTAL Donchian-20 1h breakout (`strategy.name: donchian_1h`, selectable)
2-4 week live PAPER test. Exact params from the research run (`bot/research/swing.py`, config `trend tf=1h n=20 htf=4h k=3.0 m=3.0 long_only`):
- Long only. Signal on each closed 1h bar: close > highest high of the prior 20 x 1h bars AND last closed 4h close > 4h EMA50.
- Entry at BULK mark right after the 1h close (taker 3.5 bps + book slippage). Initial stop 3 x ATR(14, 1h).
- Exit: chandelier trail = highest high since entry - 3 x ATR, ratcheted on each closed 1h bar; stop triggers on BULK mark. No TP, no max hold.
- Warm-up: at start the engine loads 600 x 1h + 300 x 4h closed BULK klines (BULK keeps ~60 days), falling back to OKX/Binance/Bybit
  if BULK returns too few, so it can trade on the very next 1h close. The start-up check never enters on an old bar.
- Same risk rules (1% risk, <=3x, max 2 open, daily cap, 4-loss breaker, stale-data breaker). `/report` compares live paper
  results with the backtest (PF 1.17, win 36.9%, 4.26 trades/week, avg R 0.064, max DD 16.3%).
- Backtest caveat: PF is thin, the first 6 months lost, and strict walk-forward did not confirm it (docs/BACKTEST.md). Paper only.

Switch back: `/set strategy.name scalper` (5m pullback scalper below, backtest: no edge).

## Scalper strategy (`strategy.name: scalper`, off)
- Trend: 15m close vs EMA50/EMA200 (long only above both, short only below) + optional session VWAP side.
- Entry (5m): pullback to EMA21 in an EMA9>EMA21 stack, bullish/bearish close beyond EMA9, RSI band, volume ≥ 1.2x avg.
  (breakout setup available, off by default.)
- Chop filter: ADX(14) ≥ 25, ATR% between 0.12 and 1.5. Optional funding skip and UTC hour skips. 10 min cooldown per symbol.
- Exits: stop 2.5×ATR (min 0.25%), TP1 at 1R closes 50% (as a maker/ALO fill), stop → breakeven, ATR trail on the rest, max hold 60m.
  Live exits are checked on BULK **mark** price (BULK triggers conditional orders on mark).
- Risk: 1% equity per trade incl. round-trip fees, notional ≤ equity × lev / max_open, leverage ≤ min(3, BULK maxLeverage),
  max 2 open (one per symbol), daily loss cap 3% (IST day), 4 losses in a row → breaker, stale-data breaker.
- Paper fills: BULK Growth tier 1 taker 3.5 bps / maker 0 bps, slippage from the live BULK L2 book (floor 1 bps),
  hourly funding, BULK tickSize / sizeIncrement / minNotional respected, isolated-margin liq price shown.

**Backtest: no edge** — see `docs/BACKTEST.md` (60 days, out-of-sample PF 0.31). Paper only.

## Telegram
/status /positions /pnl /signals /report /pause /resume /settings /set key value · manual `/long BTC [risk%]`, `/short ETH`,
`/close SOL [25|50|100]`, `/close all` — each manual action needs the inline **Confirm** button (90 s).
Alerts on every open, TP1, stop move and close.

## Future live mode (NOT enabled)
`futbot/execution.py` documents the mapping to BULK order types (market `m`, ALO limit for TP1, `st` stop, `rng` OCO,
`trl` trailing, on-fill attach). A live executor must only ever be added after explicit user confirmation, signing with
bulk-keychain using a BULK **agent wallet** key from an env secret (e.g. `/etc/memebot.env`), trade-only, never withdrawal.
`LiveExecutorDisabled` raises on every call.

## Tests / tools
```
pip install -r requirements.txt pytest
python -m pytest -q bot/tests
python bot/dry_run.py --minutes 5 --data /tmp/futdry --force-entry      # live data, no Telegram
python bot/backtest.py                                                   # ~60d download, report
python bot/research/swing.py --data DIR --out swing.md          # 15m/1h swing research, walk-forward (docs/BACKTEST.md)
python scripts/probe_sources.py                                          # which data sources answer from this machine
```

## Results and honest disclaimer

This project is **paper trading only**. It does not place real orders and it does not use a wallet. Bots can lose money. Nothing here is financial advice, and there is no guaranteed return.

A pack of the local paper results and the futures backtest summaries is in [`results/`](results/). The research write-ups are in:

- [docs/BACKTEST.md](docs/BACKTEST.md)
- [docs/BACKTEST_15M.md](docs/BACKTEST_15M.md)
- [docs/BACKTEST_HF.md](docs/BACKTEST_HF.md)

Some live server archives were not included, and those numbers are marked unknown in the results pack. Demo or paper UI screenshots are not proof of a live book.


# Proof pack for the DexBlocker trading-bot article

All figures are paper trading or backtests. No real money was traded.
Times in the CSVs are Asia/Calcutta (IST).
This pack has no Telegram tokens, no API keys, no private keys, no chat ID, no server IP, and no GCP project ID.

## Files

- `bsc_paper_summary.md` — local BSC run, 0 trades, 0% return
- `solana_local_positions.csv` — 5 closed local Solana paper positions, return as % of buy notional, win rate 80%, account return +0.33%
- `solana_local_fills.csv` — individual buy/sell fills for those 5 positions
- `solana_replay_grid.md` — replay of the same style of rules on a recorded feed
- `futures_backtests.md` / `futures_backtests.csv` — copied from repo backtest docs
- `gmgn_meme_reported.md` — dry-runs from pull requests, plus the 4 live loosened-rule trades as previously reported
- `SOURCES.md` — where each number came from, and what is still missing

## What is not in this pack

The Google Cloud server archives were not read:

- `/opt/memebot/bot/data/archive/20261102-152205-memecoin` (Solana on the server, 27 Oct to 2 Nov 2026)
- `/opt/memebot/bot/data/archive/20261108-160659-futures` (BULK futures live paper, 2 Nov to 8 Nov 2026)
- the live `gmgn_meme` database after PR #4

Those results are unknown here.

Mini App futures screenshots labelled PAPER + DEMO are not live results and are not included as proof.

## Article images

- `results_table.png` — paper results in percent
- `backtest_bars.png` — futures backtest net return
- `strict_vs_loose.png` — gate settings vs the trades I have numbers for
- `red_flags.png` — checklist for bot-profit screenshots

These match the write-up of the paper tests. They are not a live exchange ledger.


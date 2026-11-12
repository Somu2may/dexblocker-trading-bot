# Sources

| Claim | Source |
|---|---|
| BSC 0 trades, 0% return | `/workspace/bsc-memebot/data/memebot.db` (empty trades table), `bot.log` 25-26 Oct 2026 IST |
| Solana local 5 positions, 80% win, +0.33% account | `/workspace/sol-memebot/data/solbot.db` and `solbot_trades.csv`, 26-27 Oct 2026 IST |
| Solana per-position percents | realized P&L / buy notional from that same database |
| Solana replay grid | `/workspace/sol-memebot/data/grid.log` (P&L rounded to 0.001 SOL), size 0.15 SOL/trade in `replay.py` |
| Futures backtests | `docs/BACKTEST.md`, `docs/BACKTEST_15M.md`, `docs/BACKTEST_HF.md` in this repo |
| gmgn dry-runs | GitHub pull request bodies #2, #3, #4 |
| gmgn 4 live trades, 25% win, per-trade percents | previously reported from the live server DB (not re-read for this pack) |
| Server books after those dates | not read; listed as unknown |

Repo links (visible only after the owner makes the repo public):

- https://github.com/Somu2may/dexblocker-trading-bot
- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST.md
- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST_15M.md
- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST_HF.md

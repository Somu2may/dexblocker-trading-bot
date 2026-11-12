# Futures backtests (paper models, not the live GCP book)

Sources (already in the main repo, once it is public):

- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST.md
- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST_15M.md
- https://github.com/Somu2may/dexblocker-trading-bot/blob/main/docs/BACKTEST_HF.md

Raw table: `futures_backtests.csv`.

Headline numbers used in the article (repo docs also round some of these):

- 1h Donchian long-only, 700 days: 426 trades, 36.9% win rate, +27.9% net, profit factor 1.17, max drawdown 16.3%. 90-day windows were mixed: +10.72, -5.26, -7.76, +2.9, +23.61, +8.04. On BULK's own ~55 days this same 1h setup was -2.89% (23 trades, 30.4% win rate, PF 0.66).
- 15m Donchian default, 700 days: 1,820 trades, 30.1% win rate, -85.8% net, PF 0.74, max drawdown 85.8%. All 32 fifteen-minute configs in that study lost. Zero-cost check on the default was still a profit factor of 0.97.
- 1m scalper, 60 days, Binance prices with BULK fees: 130 trades, 43.8% win rate, -12.9% net, PF 0.68.
- hf_maker, Binance 245 days: 65.3% win rate, -91.8% net, 60.3 trades/day, PF 0.76.
- hf_maker, BULK 60 days: 62.3% win rate, -63.9% net, 52.5 trades/day, PF 0.61.

Docs verdict: no robust out-of-sample edge. The 1h lead is thin. The 15m and hf_maker tests lose even near zero costs.

The live futures paper book from 2 Nov to 8 Nov 2026 was archived on the server and was not read for this package. Live futures win rate and return are unknown here.

Do not use Mini App futures screenshots labelled DEMO as proof of live results.

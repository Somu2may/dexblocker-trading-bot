# Backtest report (generated 2026-11-02 14:15 IST, python bot/backtest.py)

BULK only has ~60 days of 1m klines and its early-September 2026 candles deviate strongly from the wider market (BTC p95 155 bps vs Binance; BULK volume is $3k-13k/min with 9-55% zero-volume bars). So the walk-forward uses **Binance 1m prices + funding (backtest history only)** with BULK fees; section 2 re-runs the same config on BULK's own candles. Config was picked on the in-sample window only (light grid, 384 configs; none was profitable in-sample or out-of-sample).

symbols ['BTC', 'ETH', 'SOL'] | data 2026-09-03 -> 2026-11-02 (60.0 d), in-sample until 2026-10-13, out-of-sample after

## 1) Walk-forward on binance 1m prices with BULK fees (taker 0.035%, maker 0.0%, slippage 0.01%)
|  | trades | /day | win% | PF | avg R | net $ | ret % | max DD % | fees $ |
|---|---|---|---|---|---|---|---|---|---|
| in-sample | 90 | 2.36 | 50.0 | 0.92 | -0.048 | -19.18 | -1.92 | 7.04 | 79.97 |
| out-of-sample | 40 | 2.02 | 30.0 | 0.31 | -0.397 | -111.87 | -11.19 | 12.34 | 35.09 |
| full | 130 | 2.24 | 43.8 | 0.68 | -0.155 | -128.9 | -12.89 | 16.24 | 114.39 |

per symbol (full period)
|  | trades | /day | win% | PF | avg R | net $ | ret % | max DD % | fees $ |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 43 | 0.74 | 48.8 | 0.72 | -0.119 | -31.61 | -3.16 | 4.87 | 39.08 |
| ETH | 47 | 0.81 | 34.0 | 0.44 | -0.305 | -105.69 | -10.57 | 11.48 | 41.18 |
| SOL | 40 | 0.69 | 50.0 | 1.08 | -0.017 | 8.4 | 0.84 | 4.29 | 34.13 |
exit reasons: {'stop loss': 34, 'max hold 60m': 59, 'trailing stop': 37} | longs/shorts: 64 / 66

## 2) Same config on BULK's own 1m candles (prices = BULK trades; volume filter uses reference volume)
|  | trades | /day | win% | PF | avg R | net $ | ret % | max DD % | fees $ |
|---|---|---|---|---|---|---|---|---|---|
| BULK full | 160 | 2.76 | 46.9 | 0.62 | -0.181 | -194.57 | -19.46 | 21.02 | 125.97 |
| BULK out-of-sample | 40 | 2.02 | 40.0 | 0.38 | -0.326 | -91.04 | -9.1 | 10.26 | 35.61 |

## 3) Fee comparison (full period, reference prices, same signals)
|  | trades | /day | win% | PF | avg R | net $ | ret % | max DD % | fees $ |
|---|---|---|---|---|---|---|---|---|---|
| BULK tier1 (taker 3.5 / maker 0 bps) | 130 | 2.24 | 43.8 | 0.68 | -0.155 | -128.9 | -12.89 | 16.24 | 114.39 |
| Hyperliquid base (taker 4.5 / maker 1.5 bps) | 130 | 2.24 | 43.8 | 0.61 | -0.198 | -159.58 | -15.96 | 17.85 | 147.34 |
| BULK, TP1 as taker too | 130 | 2.24 | 43.8 | 0.66 | -0.166 | -137.25 | -13.73 | 16.58 | 123.15 |

**Verdict: no edge.** Gross P&L before fees is about break-even (-$14.5 over 60 days); fees and slippage turn it into a loss, and out-of-sample is clearly worse than in-sample. A random-entry baseline was far worse, and inverting the signals gave similar results, so it is not a code bug. Keep this PAPER only.


---

# Swing / intraday research on 15m and 1h (2026-11-02)

Reproduce: `python bot/research/download.py DIR` then `python bot/research/swing.py --data <dir with BTC_15m.json ...> --out report.md` (data = Binance USDT-M 15m klines +
funding, downloaded with the free public API; ~25 months). Same cost/risk model as the paper bot: BULK taker 3.5 bps + 2 bps
slippage on every market/stop fill, maker 0 bps for limit targets (must trade through), funding, 1% risk incl. fees, <=3x,
max 2 open, 3% daily cap, 4-loss breaker. Signals on bar close, fill at next bar open, stop checked before target.

Candidates (136 configs, small grids): (a) Donchian 20/55 breakout + 4h/1d EMA50 trend filter, ATR stop 2/3, chandelier trail
3/5 ATR; (b) pullback in trend: EMA20>EMA50 + 4h filter, RSI dip below 35/45 then bullish close above EMA20, stop 1.5/2.5 ATR,
TP1 1R/2R 50% (maker) -> BE -> 2.5 ATR trail, 3 d max hold; (c) mean reversion when ADX<20: Bollinger 2/2.5 sd + RSI 30/70,
target SMA20 (maker), stop 1.5/2.5 ATR, time stop 12/48 bars; (d) regime switch: ADX>=25 -> (a), ADX<20 -> (c).
Each on 15m and 1h, long+short and long-only.

Data: Binance USDT-M 15m + funding, 2024-10-03 -> 2026-11-02; evaluation from 2024-12-02 (first 60 d = warm-up).
Walk-forward: train 180 d -> test 90 d, rolled every 90 d: 6 out-of-sample windows 2025-05-31..2025-08-29, 2025-08-29..2025-11-27, 2025-11-27..2026-02-25, 2026-02-25..2026-05-26, 2026-05-26..2026-08-24, 2026-08-24..2026-11-02.
Costs: taker 3.5 bps + 2 bps slippage, maker 0 bps, Binance funding; risk 1%/trade, <=3x, max 2 open.

## 1) Strict walk-forward (params re-picked on each 180 d train window by train return, min 15 trades; stitched OOS)
| family | trades | /week | win% | PF | avg R | ret % | max DD % |
|---|---|---|---|---|---|---|---|
| trend (1/6 OOS windows > 0) | 575 | 7.74 | 25.9 | 0.65 | -0.169 | -61.91 | 71.97 |
| pullback (3/6 OOS windows > 0) | 223 | 3.0 | 36.3 | 0.99 | 0.027 | 1.68 | 17.93 |
| mr (1/6 OOS windows > 0) | 107 | 1.44 | 38.3 | 0.52 | -0.278 | -24.67 | 25.28 |
| regime (2/6 OOS windows > 0) | 286 | 3.85 | 32.9 | 0.75 | -0.124 | -32.6 | 49.35 |
| ALL (0/6 OOS windows > 0) | 445 | 5.99 | 25.6 | 0.62 | -0.24 | -64.33 | 65.91 |
| trend_L (2/6 OOS windows > 0) | 199 | 2.68 | 28.6 | 1.11 | 0.099 | 15.23 | 30.42 |
| pullback_L (3/6 OOS windows > 0) | 163 | 2.19 | 36.8 | 1.07 | 0.086 | 10.31 | 15.84 |

Picks per window:
- **trend**: 2025-05-31: trend tf=15m n=55 htf=1d k=2.0 m=5.0 long_only=LS (train +23.3%); 2025-08-29: trend tf=1h n=20 htf=4h k=3.0 m=5.0 long_only=L (train +29.8%); 2025-11-27: trend tf=1h n=55 htf=1d k=3.0 m=5.0 long_only=LS (train +29.6%); 2026-02-25: trend tf=1h n=55 htf=4h k=2.0 m=3.0 long_only=LS (train +8.5%); 2026-05-26: trend tf=15m n=55 htf=1d k=3.0 m=5.0 long_only=LS (train -0.2%); 2026-08-24: trend tf=1h n=20 htf=4h k=2.0 m=5.0 long_only=L (train +55.2%)
- **pullback**: 2025-05-31: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +12.4%); 2025-08-29: pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L (train +13.1%); 2025-11-27: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=LS (train +29.4%); 2026-02-25: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=LS (train +6.9%); 2026-05-26: pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L (train +15.2%); 2026-08-24: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +22.4%)
- **mr**: 2025-05-31: mr tf=1h bb=2.5 rsi=30 adx=20 k=1.5 hold=12 long_only=L (train +2.8%); 2025-08-29: mr tf=1h bb=2.5 rsi=30 adx=20 k=2.5 hold=48 long_only=LS (train +1.4%); 2025-11-27: mr tf=15m bb=2.0 rsi=30 adx=20 k=1.5 hold=12 long_only=L (train -5.5%); 2026-02-25: mr tf=1h bb=2.0 rsi=30 adx=20 k=1.5 hold=48 long_only=LS (train +0.9%); 2026-05-26: mr tf=1h bb=2.0 rsi=30 adx=20 k=1.5 hold=12 long_only=L (train -5.4%); 2026-08-24: mr tf=1h bb=2.5 rsi=30 adx=20 k=2.5 hold=12 long_only=L (train -5.6%)
- **regime**: 2025-05-31: regime tf=1h n=55 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=LS (train +7.8%); 2025-08-29: regime tf=1h n=20 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=L (train +18.3%); 2025-11-27: regime tf=1h n=55 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=L (train -4.7%); 2026-02-25: regime tf=1h n=55 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=LS (train -3.9%); 2026-05-26: regime tf=1h n=20 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=L (train -19.6%); 2026-08-24: regime tf=1h n=20 htf=4h tk=3.0 m=4.0 bb=2.0 rsi=30 mk=2.0 hold=24 long_only=L (train +0.3%)
- **ALL**: 2025-05-31: trend tf=15m n=55 htf=1d k=2.0 m=5.0 long_only=LS (train +23.3%); 2025-08-29: trend tf=1h n=20 htf=4h k=3.0 m=5.0 long_only=L (train +29.8%); 2025-11-27: trend tf=1h n=55 htf=1d k=3.0 m=5.0 long_only=LS (train +29.6%); 2026-02-25: trend tf=1h n=55 htf=4h k=2.0 m=3.0 long_only=LS (train +8.5%); 2026-05-26: pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L (train +15.2%); 2026-08-24: trend tf=1h n=20 htf=4h k=2.0 m=5.0 long_only=L (train +55.2%)
- **trend_L**: 2025-05-31: trend tf=1h n=55 htf=1d k=3.0 m=3.0 long_only=L (train -1.2%); 2025-08-29: trend tf=1h n=20 htf=4h k=3.0 m=5.0 long_only=L (train +29.8%); 2025-11-27: trend tf=1h n=55 htf=1d k=2.0 m=5.0 long_only=L (train +20.9%); 2026-02-25: trend tf=1h n=55 htf=1d k=3.0 m=5.0 long_only=L (train -5.9%); 2026-05-26: trend tf=1h n=20 htf=4h k=2.0 m=5.0 long_only=L (train -1.4%); 2026-08-24: trend tf=1h n=20 htf=4h k=2.0 m=5.0 long_only=L (train +55.2%)
- **pullback_L**: 2025-05-31: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +12.4%); 2025-08-29: pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L (train +13.1%); 2025-11-27: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +20.2%); 2026-02-25: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +6.8%); 2026-05-26: pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L (train +15.2%); 2026-08-24: pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L (train +22.4%)

## 2) Every config on the full evaluation period (700 d) - top 15 by return, and per family counts
| config | trades | /week | win% | PF | avg R | ret % | max DD % | OOS windows > 0 |
|---|---|---|---|---|---|---|---|---|
| pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L | 240 | 2.4 | 39.2 | 1.21 | 0.177 | 42.4 | 15.37  4/6 |
| pullback tf=1h htf=4h rsi=45 k=2.5 tp1=2.0 m=2.5 long_only=L | 187 | 1.87 | 39.0 | 1.22 | 0.153 | 30.06 | 15.38  3/6 |
| trend tf=1h n=20 htf=4h k=3.0 m=5.0 long_only=L | 309 | 3.09 | 31.7 | 1.14 | 0.105 | 29.91 | 22.18  3/6 |
| trend tf=1h n=20 htf=4h k=2.0 m=5.0 long_only=L | 347 | 3.47 | 28.0 | 1.1 | 0.102 | 28.33 | 24.97  3/6 |
| trend tf=1h n=55 htf=1d k=3.0 m=5.0 long_only=LS | 346 | 3.46 | 31.2 | 1.1 | 0.094 | 28.14 | 25.36  3/6 |
| trend tf=1h n=20 htf=4h k=3.0 m=3.0 long_only=L | 426 | 4.26 | 36.9 | 1.17 | 0.064 | 27.92 | 16.25  4/6 |
| trend tf=1h n=20 htf=4h k=2.0 m=3.0 long_only=L | 444 | 4.44 | 34.5 | 1.11 | 0.062 | 25.9 | 19.85  4/6 |
| trend tf=1h n=55 htf=1d k=2.0 m=3.0 long_only=LS | 470 | 4.7 | 32.8 | 1.06 | 0.056 | 21.98 | 24.71  3/6 |
| trend tf=1h n=55 htf=1d k=3.0 m=3.0 long_only=LS | 452 | 4.52 | 34.7 | 1.09 | 0.046 | 18.74 | 19.2  3/6 |
| trend tf=1h n=55 htf=1d k=2.0 m=5.0 long_only=LS | 391 | 3.91 | 25.3 | 1.02 | 0.069 | 15.68 | 34.17  3/6 |
| trend tf=1h n=20 htf=1d k=3.0 m=5.0 long_only=L | 243 | 2.43 | 30.5 | 1.08 | 0.082 | 15.37 | 25.41  2/6 |
| pullback tf=1h htf=4h rsi=45 k=2.5 tp1=1.0 m=2.5 long_only=L | 212 | 2.12 | 51.9 | 1.12 | 0.071 | 14.37 | 11.77  3/6 |
| trend tf=1h n=20 htf=1d k=3.0 m=5.0 long_only=LS | 521 | 5.21 | 30.7 | 1.02 | 0.044 | 13.38 | 28.09  2/6 |
| trend tf=1h n=55 htf=1d k=3.0 m=5.0 long_only=L | 168 | 1.68 | 28.6 | 1.09 | 0.102 | 12.75 | 22.05  2/6 |
| trend tf=1h n=55 htf=4h k=2.0 m=3.0 long_only=LS | 657 | 6.57 | 33.0 | 1.02 | 0.03 | 11.94 | 33.14  2/6 |
- trend: 26/64 configs profitable over the full period; 0 with PF >= 1.2 and >= 4/6 windows positive
- pullback: 14/32 configs profitable over the full period; 4 with PF >= 1.2 and >= 4/6 windows positive
- mr: 0/32 configs profitable over the full period; 0 with PF >= 1.2 and >= 4/6 windows positive
- regime: 0/8 configs profitable over the full period; 0 with PF >= 1.2 and >= 4/6 windows positive

## 3) Neighbours of the best full-period config pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L
| neighbour | trades | /week | win% | PF | avg R | ret % | max DD % |
|---|---|---|---|---|---|---|---|
| k=2.5 | 187 | 1.87 | 39.0 | 1.22 | 0.153 | 30.06 | 15.38 |
| long_only=False | 479 | 4.79 | 35.9 | 1.02 | 0.033 | 7.3 | 27.35 |
| rsi=35 | 10 | 0.1 | 50.0 | 1.62 | 0.33 | 3.25 | 3.04 |
| tp1=1.0 | 250 | 2.5 | 50.8 | 1.01 | 0.026 | 2.29 | 16.87 |
| tf=15m | 808 | 8.08 | 34.5 | 0.81 | -0.129 | -53.83 | 55.65 |

## 4) Per symbol - best full-period config pullback tf=1h htf=4h rsi=45 k=1.5 tp1=2.0 m=2.5 long_only=L
| | trades | /week | win% | PF | avg R | ret % | max DD % |
|---|---|---|---|---|---|---|---|
| BTC | 90 | 0.9 | 37.8 | 1.0 | 0.058 | 1.03 | 13.75 |
| ETH | 85 | 0.85 | 41.2 | 1.51 | 0.362 | 33.17 | 6.99 |
| SOL | 65 | 0.65 | 38.5 | 1.13 | 0.099 | 5.84 | 6.13 |
long/short: 240/0 · exits: {'stop': 146, 'stop gap': 3, 'time': 3, 'trail': 88} · fees $202, funding $-21

## 5) Baselines over the same period
| baseline | ret % | max DD % |
|---|---|---|
| buy & hold BTC (1x, no fees) | -19.8 | 53.8 |
| buy & hold ETH (1x, no fees) | -37.8 | 69.2 |
| buy & hold SOL (1x, no fees) | -58.9 | 78.7 |

Random entries with the best config's exits (same tf, same number of signals per symbol, 20 seeds; longs only if config is long-only):
random median ret -25.85% (PF 0.88), best of 20 14.42% (PF 1.05), worst -47.9% · strategy 42.4%


## 6) Extra robustness checks (post-hoc - these look at the full period, so they are optimistic by construction)

RSI-threshold sensitivity of the best pullback config (1h, 4h filter, k 1.5, TP1 2R, long-only):
```
core pullback rsi=42: 93t 0.93/wk win 36.6 PF 0.94 avgR -0.018 ret -3.59 dd 17.5 | windows [-8.65, 3.85, 1.36, 4.33, -12.29, -1.64] | PF/sym {'BTC': 0.96, 'ETH': 0.84, 'SOL': 1.07}
core pullback rsi=43: 137t 1.37/wk win 35.0 PF 0.93 avgR -0.033 ret -6.66 dd 20.58 | windows [-5.3, 4.64, -2.73, 10.72, -12.04, -7.5] | PF/sym {'BTC': 0.96, 'ETH': 0.86, 'SOL': 0.97}
core pullback rsi=44: 196t 1.96/wk win 36.2 PF 1.02 avgR 0.033 ret 3.3 dd 23.0 | windows [1.98, 5.8, 0.83, 11.61, -10.23, -10.37] | PF/sym {'BTC': 0.86, 'ETH': 1.04, 'SOL': 1.22}
core pullback rsi=46: 295t 2.95/wk win 39.7 PF 1.2 avgR 0.164 ret 50.68 dd 16.43 | windows [8.41, 9.18, 1.84, 15.84, -0.71, -8.56] | PF/sym {'BTC': 1.03, 'ETH': 1.5, 'SOL': 1.04}
core pullback rsi=47: 349t 3.49/wk win 37.5 PF 1.1 avgR 0.093 ret 28.3 dd 17.88 | windows [5.09, 4.69, 1.98, 10.13, 0.39, -11.26] | PF/sym {'BTC': 0.9, 'ETH': 1.35, 'SOL': 1.06}
core pullback rsi=48: 394t 3.94/wk win 35.8 PF 1.02 avgR 0.041 ret 8.1 dd 22.81 | windows [4.16, 2.89, 0.25, 10.25, -3.68, -14.3] | PF/sym {'BTC': 0.8, 'ETH': 1.27, 'SOL': 1.02}
core pullback rsi=42 m=3.5: 93t 0.93/wk win 36.6 PF 0.91 avgR -0.041 ret -5.33 dd 17.81 | windows [-7.77, 4.05, 0.2, 4.65, -12.61, -0.55] | PF/sym {'BTC': 0.91, 'ETH': 0.85, 'SOL': 1.0}
core pullback rsi=44 m=3.5: 195t 1.95/wk win 36.4 PF 1.05 avgR 0.058 ret 7.31 dd 23.33 | windows [11.79, 5.54, -1.41, 11.62, -10.96, -9.43] | PF/sym {'BTC': 0.94, 'ETH': 1.07, 'SOL': 1.17}
core pullback rsi=46 m=3.5: 290t 2.9/wk win 40.0 PF 1.3 avgR 0.232 ret 79.15 dd 17.82 | windows [22.13, 8.76, 2.01, 15.96, 6.35, -5.04] | PF/sym {'BTC': 1.13, 'ETH': 1.68, 'SOL': 1.01}
core pullback rsi=48 m=3.5: 386t 3.86/wk win 36.5 PF 1.1 avgR 0.107 ret 35.21 dd 24.86 | windows [23.05, 3.05, 0.14, 10.09, 2.41, -11.19] | PF/sym {'BTC': 0.87, 'ETH': 1.42, 'SOL': 1.03}
```
Same rules on a different universe (BNB, XRP, DOGE, LINK, AVAX, ADA; never used for picking):
```
ALT-universe pullback rsi=40: 86t 0.86/wk win 26.7 PF 0.54 avgR -0.33 ret -24.95 dd 28.25 | windows [-2.06, -2.18, -1.13, -2.43, -5.73, -1.03] | PF/sym {'BNB': 0.62, 'XRP': 0.61, 'DOGE': 1.15, 'LINK': 0.0, 'AVAX': 0.64, 'ADA': 1.71}
ALT-universe pullback rsi=45: 349t 3.49/wk win 30.4 PF 0.77 avgR -0.161 ret -44.59 dd 46.67 | windows [-2.85, -2.91, -13.05, 6.47, -13.81, -14.68] | PF/sym {'BNB': 0.89, 'XRP': 0.28, 'DOGE': 0.68, 'LINK': 0.67, 'AVAX': 1.02, 'ADA': 1.48}
ALT-universe pullback rsi=50: 643t 6.43/wk win 28.9 PF 0.77 avgR -0.18 ret -70.29 dd 72.78 | windows [-11.36, -17.15, -26.64, 7.13, -19.61, -27.5] | PF/sym {'BNB': 0.74, 'XRP': 0.51, 'DOGE': 1.3, 'LINK': 0.71, 'AVAX': 0.65, 'ADA': 1.22}
ALT-universe pullback rsi=45 m=3.5: 341t 3.41/wk win 30.5 PF 0.8 avgR -0.134 ret -39.18 dd 41.0 | windows [1.44, 7.03, -14.42, 0.21, -11.66, -15.73] | PF/sym {'BNB': 1.05, 'XRP': 0.32, 'DOGE': 0.5, 'LINK': 0.63, 'AVAX': 1.19, 'ADA': 1.29}
ALT-universe pullback rsi=45 long+short: 755t 7.55/wk win 34.8 PF 0.97 avgR -0.007 ret -12.05 dd 38.81 | windows [3.16, 29.62, -2.72, 15.94, -24.41, -14.33] | PF/sym {'BNB': 0.97, 'XRP': 0.95, 'DOGE': 0.99, 'LINK': 0.83, 'AVAX': 1.03, 'ADA': 1.12}
ALT-universe trend 1h n20 4h k3 m3 L: 582t 5.82/wk win 34.5 PF 1.07 avgR 0.031 ret 15.72 dd 21.08 | windows [21.11, -3.48, 5.46, -4.68, 19.85, -1.82] | PF/sym {'BNB': 0.97, 'XRP': 1.33, 'DOGE': 1.05, 'LINK': 1.3, 'AVAX': 0.92, 'ADA': 0.83}
ALT-universe trend 1h n20 4h k3 m3 LS: 1158t 11.58/wk win 35.1 PF 0.99 avgR 0.004 ret -2.3 dd 23.97 | windows [14.87, 0.61, 8.31, -14.82, 5.21, -1.49] | PF/sym {'BNB': 0.88, 'XRP': 1.15, 'DOGE': 1.03, 'LINK': 1.14, 'AVAX': 0.89, 'ADA': 0.82}
```
Cost stress:
```
pullback slip 2bps | full 240t PF 1.21 ret 42.4 dd 15.37 | pre-OOS PF 1.31 ret 12.42 | OOS-period PF 1.19 ret 26.67 dd 12.66 | windows [9.98, 9.29, -2.32, 17.7, 4.0, -11.86]
trend slip 2bps | full 426t PF 1.17 ret 27.92 dd 16.25 | pre-OOS PF 0.9 ret -3.79 | OOS-period PF 1.27 ret 32.96 dd 16.25 | windows [10.72, -5.26, -7.76, 2.9, 23.61, 8.04]
pullback slip 5bps | full 241t PF 1.08 ret 16.52 dd 19.57 | pre-OOS PF 1.31 ret 12.86 | OOS-period PF 1.02 ret 3.25 dd 19.57 | windows [6.73, 7.34, -6.68, 15.72, -4.77, -12.37]
trend slip 5bps | full 426t PF 1.08 ret 13.48 dd 18.99 | pre-OOS PF 0.84 ret -6.36 | OOS-period PF 1.17 ret 21.19 dd 18.99 | windows [8.69, -6.6, -9.13, 1.17, 21.61, 6.79]
pullback Hyperliquid fees | full 240t PF 1.18 ret 35.86 dd 15.67 | pre-OOS PF 1.28 ret 11.12 | OOS-period PF 1.16 ret 22.26 dd 12.92 | windows [9.2, 8.42, -2.7, 16.82, 3.23, -12.0]
trend Hyperliquid fees | full 426t PF 1.14 ret 22.45 dd 17.06 | pre-OOS PF 0.88 ret -4.7 | OOS-period PF 1.24 ret 28.49 dd 17.06 | windows [9.94, -5.69, -8.15, 2.3, 22.64, 7.54]
```

## Verdict: no robust out-of-sample edge - do not deploy, keep paper only

- Strict walk-forward (parameters re-picked on each 180-day training window): every family is flat or negative out of sample -
  trend -62 %, pullback +1.7 % (PF 0.99), mean reversion -25 %, regime -33 %, all families pooled -64 %.
  Restricting to long-only (a hindsight choice in a period where all three coins fell 20-59 %): trend +15 % (PF 1.11, DD 30 %,
  only 2/6 windows positive), pullback +10 % (PF 1.07, 3/6 windows).
- The best single config (1h pullback, RSI<45, long-only: +42 %, PF 1.21, 2.4 trades/week, beats random entries, median -26 %)
  is fragile: RSI 42-44 or 48 instead of 45 drops it to about zero, BTC alone is flat (PF 1.0), the latest window
  (Aug-Nov 2026) loses for every neighbour, and on the alt universe the same rules lose 45 % (PF 0.77).
- Mean reversion lost money in all 32 configs; the regime switch in all 8. 15m versions are worse than 1h everywhere (costs).
- Most promising lead for later: 1h Donchian-20 long-only with 4h trend filter and 3 ATR stop/trail - positive on both the core
  (+28 %, PF 1.17) and the alt universe (+16 %, PF 1.07) and still positive at 5 bps slippage, but PF is thin, it lost in the
  first 6 months and strict walk-forward does not confirm it. Not implemented in the bot.


## 2026-11-02 update: Donchian-1h lead implemented as EXPERIMENTAL paper strategy
At the owner's request the lead above (`trend tf=1h n=20 htf=4h k=3.0 m=3.0 long_only`) is now the bot's default strategy
(`strategy.name: donchian_1h`) for a 2-4 week live paper test. The live signal code (`futbot/strategy.donchian_latest`) was
checked against the research simulator on 7,260 sampled 1h bars of BTC/ETH/SOL: 212 signals, 0 mismatches in side or stop
distance (with 600 x 1h / 300 x 4h warm-up). Expectations used by /report (full 700-day backtest): PF 1.17, win 36.9 %,
4.26 trades/week across 3 symbols, avg R +0.064, max DD 16.3 %, 90-day windows between -7.8 % and +23.6 %.
Note: BULK's own 1h candles differ slightly from Binance/OKX (thin book; e.g. ETH 20h high 2,312.5 on BULK vs 2,318.1 on OKX),
so live signals can differ from a Binance-based backtest on marginal breakouts.

## 2026-11-02 update 2: 15m Donchian made default at the owner's request (paper only) - backtest is NEGATIVE
Full table: docs/BACKTEST_15M.md (`bot/research/donchian15.py`, same simulator/costs, 700 d, 3 symbols).
| config | trades/week | win % | PF | net % | max DD % |
|---|---|---|---|---|---|
| 1h n=20, 4h EMA50, 3/3 ATR, long-only (donchian_1h) | 4.26 | 36.9 | 1.17 | +27.9 | 16.3 |
| **15m n=20, 1h EMA50, 3/3 ATR, long-only (donchian_15m, default)** | 18.2 | 30.1 | 0.74 | -85.8 | 85.8 |
| 15m same, long + short | 32.4 | 30.2 | 0.80 | -94.7 | 94.8 |
| least-bad of 32 15m configs (n=55, 4h EMA50, 3/5 ATR, long-only) | 7.2 | 28.4 | 0.81 | -46.1 | 60.7 |
All 32 15m configs lose; every 90-day window of the default 15m config is negative; with zero costs PF is still 0.97.
On BULK's own last 55 days: 1h PF 0.66 (-2.9 %, 23 trades), 15m PF 0.62 (-17.8 %, 135 trades).

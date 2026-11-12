"""Research only: cross-check bot/futbot/metrics.py against real QuantStats, and a VectorBT sanity backtest.
Needs requirements-research.txt (NOT installed on the VM).  python bot/research/qs_check.py
"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import quantstats as qs
from futbot import metrics

def check(seed=1):
    r = list(np.random.default_rng(seed).normal(0.001, 0.01, 60))
    s = pd.Series(r, index=pd.date_range("2026-01-01", periods=len(r)))
    ours = (metrics.sharpe(r), metrics.sortino(r), metrics.max_drawdown(r))
    theirs = (qs.stats.sharpe(s, periods=365), qs.stats.sortino(s, periods=365), qs.stats.max_drawdown(s))
    return ours, theirs

def vbt_demo():
    import vectorbt as vbt
    px = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.01, 500))))
    f, s = vbt.MA.run(px, 10), vbt.MA.run(px, 30)
    return vbt.Portfolio.from_signals(px, f.ma_crossed_above(s), f.ma_crossed_below(s), fees=0.00035).stats()

if __name__ == "__main__":
    o, t = check(); print("ours  ", o); print("qs    ", t)
    assert all(abs(a - b) < 1e-6 for a, b in zip(o, t)), "mismatch"
    print("QuantStats formulas match ✅"); print(vbt_demo())

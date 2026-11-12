"""Lightweight QuantStats-compatible report metrics (pure Python, no pandas/scipy).

The QuantStats package itself (quantstats==0.0.86) pulls pandas+scipy+matplotlib+seaborn+yfinance: ~470 MB on disk and
~180 MB RSS just to import - too heavy for the e2-micro VM (1 GB RAM). So the live bot uses these re-implementations of
QuantStats' formulas (qs.stats.sharpe / sortino / max_drawdown / win_rate / profit_factor on daily returns, 365 periods
for 24/7 crypto, rf=0). bot/research/qs_check.py cross-checks them against the real QuantStats (research-only dependency).
"""
import datetime as dt, math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MIN_TRADES, MIN_DAYS = 5, 3          # below this, risk ratios are noise -> reported as "n/a"


def daily_returns(trades, start_balance, since, now):
    """Daily equity returns (IST days, flat days included) from closed paper trades."""
    d0 = dt.datetime.fromtimestamp(since, IST).date(); d1 = dt.datetime.fromtimestamp(now, IST).date()
    pnl = {}
    for t in trades:
        d = dt.datetime.fromtimestamp(t["closed"], IST).date(); pnl[d] = pnl.get(d, 0.0) + float(t["pnl"] or 0)
    out, eq, d = [], float(start_balance), d0
    while d <= d1:
        p = pnl.get(d, 0.0); out.append(p / eq if eq > 0 else 0.0); eq += p; d += dt.timedelta(days=1)
    return out


def _std(x):
    m = sum(x) / len(x); return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def sharpe(r, periods=365):
    if len(r) < 2: return None
    s = _std(r); return None if s == 0 else sum(r) / len(r) / s * math.sqrt(periods)


def sortino(r, periods=365):
    if len(r) < 2: return None
    dd = math.sqrt(sum(v * v for v in r if v < 0) / len(r))
    return None if dd == 0 else sum(r) / len(r) / dd * math.sqrt(periods)


def max_drawdown(r):
    eq = peak = 1.0; mdd = 0.0
    for v in r: eq *= 1 + v; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    return mdd


def report_metrics(trades, start_balance, since, now):
    """Never raises; ratios are None when the sample is too small."""
    try:
        n = len(trades); pn = [float(t["pnl"] or 0) for t in trades]
        g = sum(p for p in pn if p > 0); l = -sum(p for p in pn if p < 0)
        r = daily_returns(trades, start_balance, since, now)
        ok = n >= MIN_TRADES and len(r) >= MIN_DAYS
        rnd = lambda v, k=2: None if v is None else round(v, k)
        return {"days": len(r), "sharpe": rnd(sharpe(r)) if ok else None, "sortino": rnd(sortino(r)) if ok else None,
                "max_dd_daily_pct": round(max_drawdown(r) * 100, 2) if r else None,
                "win_rate_pct": round(sum(p > 0 for p in pn) / n * 100, 1) if n else None,
                "profit_factor": (round(g / l, 2) if l else (99.0 if g else None)), "enough": ok}
    except Exception:
        return {"days": 0, "sharpe": None, "sortino": None, "max_dd_daily_pct": None, "win_rate_pct": None, "profit_factor": None, "enough": False}


def metrics_text(m):
    f = lambda v, s="": "n/a" if v is None else f"{v}{s}"
    head = "📐 Risk metrics (QuantStats formulas, daily, 365d/yr)"
    if not m["enough"]: head += f" - small sample, need {MIN_TRADES}+ trades & {MIN_DAYS}+ days"
    return (f"{head}\nSharpe {f(m['sharpe'])} · Sortino {f(m['sortino'])} · max DD (daily) {f(m['max_dd_daily_pct'], '%')}\n"
            f"Win rate {f(m['win_rate_pct'], '%')} · profit factor {f(m['profit_factor'])} · {m['days']} day(s)")

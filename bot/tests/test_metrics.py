import datetime as dt
from futbot import metrics
IST = metrics.IST
def ts(d, h=12): return dt.datetime(2026, 11, d, h, tzinfo=IST).timestamp()
def T(d, pnl): return {"closed": ts(d), "pnl": pnl}

def test_no_trades_does_not_break():
    m = metrics.report_metrics([], 1000, ts(1), ts(1)); assert m["sharpe"] is None and m["win_rate_pct"] is None and not m["enough"]
    assert "n/a" in metrics.metrics_text(m)

def test_too_few_trades_ratios_na():
    m = metrics.report_metrics([T(1, 5), T(2, -3)], 1000, ts(1), ts(4))
    assert m["sharpe"] is None and m["sortino"] is None and m["win_rate_pct"] == 50.0 and m["profit_factor"] == 1.67

def test_full_metrics():
    tr = [T(1, 10), T(2, -5), T(3, 8), T(4, -2), T(5, 4), T(5, -1)]
    m = metrics.report_metrics(tr, 1000, ts(1), ts(6))
    assert m["enough"] and m["days"] == 6 and m["sharpe"] > 0 and m["sortino"] > m["sharpe"]
    assert m["profit_factor"] == round(22 / 8, 2) and m["win_rate_pct"] == 50.0 and m["max_dd_daily_pct"] < 0

def test_bad_data_never_raises():
    m = metrics.report_metrics([{"closed": "x", "pnl": 1}], 1000, ts(1), ts(2)); assert m["enough"] is False

def test_known_values_match_quantstats_formula():
    r = [0.01, -0.02, 0.015, 0.0, -0.005]
    assert round(metrics.max_drawdown(r), 6) == round((1.01 * 0.98) / 1.01 - 1, 6)
    assert metrics.sharpe([0.01, 0.01]) is None and metrics.sortino([0.01, 0.02]) is None

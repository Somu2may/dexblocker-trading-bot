import asyncio, copy, os, tempfile, time, pytest
from futbot import config, strategy, portfolio
from futbot.engine import Engine
from futbot.store import Store

P = config.load()["strategy"]["donchian"]

def bars(n, sec, start, step, t0, wiggle=0.0):
    out, p = [], start
    for i in range(n):
        o = p; p = p + step + (wiggle if i % 2 else -wiggle); out.append((t0 + i * sec, o, max(o, p) + 1, min(o, p) - 1, p, 100.0))
    return out

def setup(up=True, breakout=True, n1=600, n4=300):
    now = int(time.time() // 3600 * 3600)
    b4 = bars(n4, 14400, 1000.0, 2.0 if up else -2.0, now - n4 * 14400)
    b1 = bars(n1, 3600, 1500.0, 0.0, now - n1 * 3600, wiggle=3.0)          # flat range ...
    last = b1[-1]; px = last[4] + (30 if breakout else 0)
    b1[-1] = (last[0], last[1], max(last[2], px), last[3], px, 100.0)       # ... with a breakout close on the last bar
    return b1, b4

def test_long_breakout_with_4h_uptrend():
    b1, b4 = setup(); d = strategy.donchian_latest(b1, b4, P)
    assert d["side"] == 1 and d["setup"] == "donchian" and d["diag"]["trend"] == 1
    assert d["stop_dist"] == pytest.approx(3.0 * d["atr"]) and d["diag"]["dist_pct"] < 0

def test_no_long_in_4h_downtrend_and_long_only():
    b1, b4 = setup(up=False); d = strategy.donchian_latest(b1, b4, P)
    assert d["side"] == 0 and d["diag"]["trend"] == -1 and "no long" in d["reason"]

def test_waiting_shows_distance_and_warmup():
    b1, b4 = setup(breakout=False); d = strategy.donchian_latest(b1, b4, P)
    assert d["side"] == 0 and d["diag"]["dist_pct"] > 0 and d["reason"].startswith("waiting")
    assert "warming up" in strategy.donchian_latest(b1[:10], b4, P)["reason"]
    assert "warming up" in strategy.donchian_latest(b1, b4[:20], P)["reason"]

def test_closed_only_drops_open_bar():
    now = 10_000; b = [(3600 * 1, 1, 1, 1, 1, 1), (now - 100, 1, 1, 1, 1, 1)]
    assert strategy.closed_only(b, 3600, now) == b[:1]

def test_4h_bar_not_used_before_it_closes():
    b1, b4 = setup(); t_close = b1[-1][0] + 3600
    extra = (b4[-1][0] + 14400, 0, 0, 0, 1.0, 0)                            # a 4h bar still open at the 1h close -> ignored
    if extra[0] + 14400 > t_close:
        assert strategy.donchian_latest(b1, b4 + [extra], P)["diag"]["close4h"] == b4[-1][4]

def pf():
    c = copy.deepcopy(config.load()); p = portfolio.Portfolio(c)
    p.rules = {"BTC": {"tickSize": 0.01, "sizeIncrement": 1e-8, "minNotional": 1, "maxLeverage": 40}}; return p

def test_trend_position_no_tp_no_max_hold_and_ratchet():
    p = pf(); pos = p.open("BTC", 1, 50000, 1500, 0, atr=500, setup="donchian", mode="trend", trail_mult=3.0)
    assert pos.tp1 is None and pos.mode == "trend" and pos.stop == pytest.approx(pos.entry - 1500, abs=0.02)
    p.on_price("BTC", 70000, 10 * 86400)                                    # far above, 10 days later: still open, no TP, no max hold
    assert "BTC" in p.positions and pos.stop == pytest.approx(pos.entry - 1500, abs=0.02)  # trail moves only on closed bars
    assert p.trail_bar("BTC", 52000, 49900, 500, 3.0) == pytest.approx(50500)
    assert p.trail_bar("BTC", 51000, 50000, 600, 3.0) is None              # never loosens
    p.on_price("BTC", 50499, 11 * 86400); t = p.closed[-1]
    assert t["reason"] == "trailing stop" and "BTC" not in p.positions

def test_config_strategy_name():
    c = config.load(); assert c["strategy"]["name"] == "gmgn_meme"
    assert config.set_key(copy.deepcopy(c), "strategy.name", "hf_maker") == "hf_maker"
    assert config.set_key(c, "strategy.name", "scalper") == "scalper"
    assert config.set_key(c, "strategy.name", "donchian_1h") == "donchian_1h"
    with pytest.raises(ValueError): config.set_key(c, "strategy.name", "yolo")
    d = c["strategy"]["donchian"]
    assert (d["tf"], d["trend_tf"], d["channel"], d["trend_ema"], d["stop_atr"], d["trail_atr"], d["long_only"]) == ("1h", "4h", 20, 50, 3.0, 3.0, True)

class FakeFeed:
    def __init__(self, b1, b4, empty=False):
        self.b = {"1h": b1, "4h": b4}; self.empty = empty; self.active = "bulk"; self.client = None; self.proxy = None; self.srcs = {}
        self.price = {"BTC": b1[-1][4]}; self.mark = {"BTC": b1[-1][4]}; self.oracle = {}; self.funding = {}; self.status = {}; self.ws_ok = True; self.switches = 0
    async def candles(self, sym, tf, n): return [] if self.empty else self.b[tf][-n:]
    def age(self, s): return 1.0

def engine(feed, name="donchian_1h"):
    c = copy.deepcopy(config.load()); c["data"]["volume_ref_sources"] = []; c["strategy"]["name"] = name
    for blk in ("donchian", "donchian_15m"): c["strategy"][blk]["fallback_history"] = []    # never hit the network in tests
    c["symbols"] = c["enabled_symbols"] = ["BTC"]
    st = Store(os.path.join(tempfile.mkdtemp(), "f.db")); e = Engine(c, st, feed=feed); e.pf.rules = {"BTC": {"tickSize": 0.01, "sizeIncrement": 1e-8, "minNotional": 1, "maxLeverage": 40}}
    return e

def test_engine_warmup_then_entry_and_report():
    b1, b4 = setup(); e = engine(FakeFeed(b1, b4))
    asyncio.run(e.evaluate("BTC", warmup=True))
    assert e.signals["BTC"]["side"] == 1 and "warm-up" in e.signals["BTC"]["action"] and not e.pf.positions
    asyncio.run(e.evaluate("BTC"))
    pos = e.pf.positions["BTC"]; assert pos.mode == "trend" and pos.setup == "donchian" and pos.lev <= 3
    assert e.snapshot()["strategy"]["experimental"] and "bulk" in e.hist_src["BTC 1h"]
    r = e.report(); assert r["live"]["trades"] == 0 and "too early" in r["verdict"] and r["expect"]["pf"] == 1.17
    assert "backtest" in e.report_text()

def test_engine_reports_missing_history(monkeypatch):
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real(0))
    b1, b4 = setup(); e = engine(FakeFeed(b1, b4, empty=True))
    asyncio.run(e.evaluate("BTC")); assert "warming up" in e.signals["BTC"]["reason"] and not e.pf.positions

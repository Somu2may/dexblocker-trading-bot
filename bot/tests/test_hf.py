"""hf_maker (default EXPERIMENT): signal parity with research, maker fills on trade-through, exits, overrides, breakers, report, chart API."""
import asyncio, copy, os, random, sys, time, pytest
from futbot import config, strategy
from test_donchian import engine
from test_miniapp import c as client, init_data
import adapter

HP = config.load()["strategy"]["hf"]

def walk(n=700, start=100.0, seed=1):
    r = random.Random(seed); t0 = int(time.time() // 60 * 60) - n * 60; out, p = [], start
    for i in range(n):
        o = p; p = max(1.0, p + r.gauss(0, 0.08)); h = max(o, p) + abs(r.gauss(0, 0.04)); l = min(o, p) - abs(r.gauss(0, 0.04))
        out.append((t0 + i * 60, o, h, l, p, 0.0))
    m = strategy._ema_seq([b[4] for b in out], 60)[-1]; t, o, h, l, _, v = out[-1]      # last close at the mean -> both sides quoted
    out[-1] = (t, o, max(h, m), min(l, m), m, v)
    return out

class Feed1m:
    def __init__(self, bars):
        self.b = bars; self.active = "bulk"; self.client = None; self.proxy = None; self.srcs = {}
        self.price = {"BTC": bars[-1][4]}; self.mark = {"BTC": bars[-1][4]}; self.oracle = {}; self.funding = {}; self.status = {}; self.ws_ok = True; self.switches = 0
    async def candles(self, sym, tf, n): assert tf == "1m"; return self.b[-n:]
    def age(self, s): return 1.0

def hf_engine(bars=None):
    e = engine(Feed1m(bars or walk()), "hf_maker"); e.apply_overrides(); return e

def test_default_is_hf_maker_and_others_selectable():
    c = config.load(); assert c["strategy"]["name"] == "gmgn_meme"
    assert config.set_key(copy.deepcopy(c), "strategy.name", "hf_maker") == "hf_maker"
    for n in ("donchian_1h", "donchian_15m", "scalper"): assert config.set_key(copy.deepcopy(c), "strategy.name", n) == n
    h = c["strategy"]["hf"]; assert h["risk"]["risk_pct"] <= 0.5 and h["risk"]["max_open_positions"] == 3 and c["risk"]["leverage"] <= 3
    assert c["risk"]["daily_loss_cap_pct"] == 3.0 and h["expect"]["pf"] < 1 and h["target_trades_per_day"] == 50

def test_quotes_match_research_math():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research")); import hf, numpy as np
    bars = walk(900); a = np.array(bars); p = {**hf.LIVE}
    F = hf.Feat(a, p)
    for i in (700, 800, 899):
        d = strategy.hf_quotes(bars[max(0, i - 599):i + 1], HP)               # live window: 600 bars
        m, at = F.mean[i], F.atr[i]
        assert d["diag"]["mean"] == pytest.approx(m, rel=1e-6) and d["atr"] == pytest.approx(at, rel=1e-4)
        assert d["diag"]["bid"] == pytest.approx(m - p["k"] * at, rel=1e-5) and d["stop_dist"] == pytest.approx(p["s"] * at, rel=1e-4)
        for sd, lvl, _, tgt in d["quotes"]: assert (bars[i][4] - lvl) * sd > 0 and tgt == pytest.approx(m, rel=1e-6)   # passive side only
    assert "warming up" in strategy.hf_quotes(bars[:100], HP)["reason"]

def test_overrides_risk_and_max_open():
    e = hf_engine(); assert e.pf.rk("risk_pct") == 0.25 and e.pf.rk("max_open_positions") == 3 and e.entry_sec() == 60
    e.cfg["strategy"]["name"] = "donchian_1h"; e.apply_overrides(); assert e.pf.rk("risk_pct") == 1.0 and e.pf.rk("max_open_positions") == 2

def quote(e):
    asyncio.run(e.evaluate("BTC")); d = e.signals["BTC"]; return d, e.quotes.get("BTC")

def test_warmup_no_quotes_then_quotes():
    e = hf_engine(); asyncio.run(e.evaluate("BTC", warmup=True)); assert "BTC" not in e.quotes and "warm-up" in e.signals["BTC"]["action"]
    d, q = quote(e); assert q and d["action"].startswith("quotes resting") and e.snapshot()["quotes"]["BTC"]["bid"]

def test_maker_fill_needs_trade_through_then_target_maker():
    e = hf_engine(); d, q = quote(e); now = time.time()
    bid = next(x for x in q["q"] if x[0] > 0); lvl, tgt = bid[1], bid[3]
    e.feed.mark["BTC"] = lvl; e.check_quotes(now); assert "BTC" not in e.pf.positions            # touch is not a fill
    e.feed.mark["BTC"] = lvl * (1 - 0.0002); e.check_quotes(now)
    p = e.pf.positions["BTC"]; assert p.mode == "hf" and p.entry == pytest.approx(lvl) and p.fees == 0 and p.maker_entry and p.tp1 == pytest.approx(tgt, abs=0.011)
    assert "BTC" not in e.quotes                                                                    # other side cancelled
    risk = abs(p.entry - p.stop) * p.qty; assert risk <= 1000 * 0.0026 and p.qty * p.entry <= 1000 * 3 / 3 + 1
    e.pf.on_price("BTC", p.tp1, now + 60); assert "BTC" in e.pf.positions                             # touch target: no fill
    e.pf.on_price("BTC", p.tp1 * 1.0002, now + 61); t = e.pf.closed[-1]
    assert t["reason"] == "target (maker)" and t["fees"] == 0 and t["exit"] == pytest.approx(tgt, abs=0.011)

def test_stop_and_time_exit_are_taker():
    e = hf_engine(); quote(e); now = time.time(); q = e.quotes["BTC"]["q"]; sd, lvl, sdist, tgt = q[0]
    e.feed.mark["BTC"] = lvl * (1 - sd * 0.001); e.check_quotes(now); p = e.pf.positions["BTC"]
    e.pf.on_price("BTC", p.stop - sd * 0.01, now + 30); t = e.pf.closed[-1]; assert t["reason"] == "stop loss" and t["fees"] > 0
    e2 = hf_engine(); quote(e2); q = e2.quotes["BTC"]["q"]; sd, lvl, _, _ = q[0]
    e2.feed.mark["BTC"] = lvl * (1 - sd * 0.001); e2.check_quotes(now); p = e2.pf.positions["BTC"]
    e2.pf.on_price("BTC", p.entry, now + 121 * 60); assert e2.pf.closed[-1]["reason"].startswith("time exit")

def test_quotes_expire_and_blocked_by_daily_cap():
    e = hf_engine(); quote(e); e.check_quotes(time.time() + 200); assert "BTC" not in e.quotes
    e.pf.breakers.tripped = "daily loss cap 3.0% hit"; d, q = quote(e); assert q is None and "daily loss cap" in d["action"]

def test_loss_streak_pauses_30_min_not_whole_day():
    e = hf_engine(); b = e.pf.breakers; now = time.time()
    for _ in range(4): b.on_close(-1.0, now)
    assert b.tripped is None and b.paused(now + 60) and not b.paused(now + 31 * 60) and b.pauses == 1
    d, q = quote(e); assert q is None and "loss-streak pause" in d["action"]
    e.cfg["strategy"]["name"] = "donchian_1h"; e.apply_overrides(); b.pause_until = 0
    for _ in range(4): b.on_close(-1.0, now)
    assert b.tripped == "4 consecutive losses"                                                      # other strategies: old rule

def test_hf_trades_not_pushed_but_manual_are_and_report_shows_trades_per_day():
    e = hf_engine(); sent = []; e.notify = sent.append; quote(e); now = time.time(); sd, lvl, _, _ = e.quotes["BTC"]["q"][0]
    e.feed.mark["BTC"] = lvl * (1 - sd * 0.001); e.check_quotes(now); e.pf.on_price("BTC", e.pf.positions["BTC"].stop - sd, now + 5)
    assert not sent and e.report()["live"]["trades"] == 1
    t = e.report_text(); print(t); assert "Risk metrics" in t and "trades/day" in t and "target 50" in t and "Daily 3% loss cap" in t
    assert e._push({"manual": True, "mode": "scalp"}) and not e._push({"manual": False, "mode": "hf"})

H = lambda: {"X-Telegram-Init-Data": init_data()}

def test_candles_api_demo_has_markers_and_lines():
    for tf in ("1m", "5m", "15m"):
        j = client.get(f"/api/candles?symbol=BTC&tf={tf}", headers=H()).json()
        assert j["demo"] and len(j["candles"]) == 240 and j["candles"][-1]["close"] == pytest.approx(78012.4, rel=1e-6)
        sh = {m["shape"] for m in j["markers"]}; assert {"arrowUp", "arrowDown", "circle"} <= sh
        assert {"stop", "target", "entry"} <= {l["title"] for l in j["lines"]}
        assert all(j["markers"][i]["time"] <= j["markers"][i + 1]["time"] for i in range(len(j["markers"]) - 1))
    assert "maker bid" in {l["title"] for l in client.get("/api/candles?symbol=ETH&tf=1m", headers=H()).json()["lines"]}
    assert client.get("/api/candles?symbol=DOGE", headers=H()).status_code == 422
    assert client.get("/api/candles?symbol=BTC&tf=1h", headers=H()).status_code == 422
    assert client.get("/api/candles?symbol=BTC").status_code == 401

def test_chart_markers_from_trades_and_positions():
    T = [{"symbol": "BTC", "side": -1, "opened": 1000, "closed": 1300, "pnl": -2.0, "manual": 0}, {"symbol": "ETH", "side": 1, "opened": 1000, "closed": 1100, "pnl": 1, "manual": 0}]
    P = [{"symbol": "BTC", "side": 1, "opened": 1500, "entry": 10.0, "stop": 9.0, "tp1": 11.0, "tp1_done": False}]
    mk, lines = adapter.chart_markers(T, P, {"BTC": {"bid": 9.5, "ask": 10.5}}, "BTC", 900, 60)
    assert [m["shape"] for m in mk] == ["arrowDown", "circle", "arrowUp"] and mk[0]["time"] == 960 and mk[1]["text"] == "-2.00"
    assert [l["title"] for l in lines] == ["entry", "stop", "target", "maker bid", "maker ask"]

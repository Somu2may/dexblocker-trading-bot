"""15m Donchian variant (default) + automatic report schedule."""
import asyncio, copy, datetime as dt, os, sys, tempfile, time, pytest, yaml
from futbot import config, strategy
from futbot.portfolio import IST
from test_donchian import bars, engine

P15 = config.load()["strategy"]["donchian_15m"]

class Feed15:
    def __init__(self, b15, b1h):
        self.b = {"15m": b15, "1h": b1h}; self.active = "bulk"; self.client = None; self.proxy = None; self.srcs = {}
        self.price = {"BTC": b15[-1][4]}; self.mark = {"BTC": b15[-1][4]}; self.oracle = {}; self.funding = {}; self.status = {}; self.ws_ok = True; self.switches = 0
        self.asked = []
    async def candles(self, sym, tf, n): self.asked.append((tf, n)); return self.b[tf][-n:]
    def age(self, s): return 1.0

def setup15(up=True, breakout=True, down_break=False, n15=600, n1=300):
    now = int(time.time() // 900 * 900)
    b1 = bars(n1, 3600, 1000.0, 2.0 if up else -2.0, now - n1 * 3600)
    b15 = bars(n15, 900, 1500.0, 0.0, now - n15 * 900, wiggle=3.0)
    last = b15[-1]; px = last[4] + (30 if breakout else 0) - (60 if down_break else 0)
    b15[-1] = (last[0], last[1], max(last[2], px), min(last[3], px), px, 100.0)
    return b15, b1

def test_default_config_is_15m_with_1h_trend_and_warmup():
    c = config.load(); d = strategy.donchian_cfg(c["strategy"])
    d = strategy.donchian_cfg(c["strategy"], "donchian_15m")
    assert (d["tf"], d["trend_tf"], d["channel"], d["trend_ema"]) == ("15m", "1h", 20, 50)
    assert (d["stop_atr"], d["trail_atr"]) == (3.0, 3.0) and d["long_only"] is True
    assert strategy.hist_n(d, "entry") >= 600 and strategy.hist_n(d, "trend") >= 300
    assert c["risk"]["leverage"] <= 3 and c["risk"]["risk_pct"] == 1.0 and c["risk"]["max_open_positions"] == 2
    assert config.set_key(c, "strategy.name", "donchian_15m") == "donchian_15m"
    assert d["expect"]["pf"] < 1                                           # honest: the backtest is negative
    assert strategy.donchian_cfg(c["strategy"], "donchian_1h")["tf"] == "1h"   # 1h variant still selectable

def test_15m_long_breakout_uses_1h_trend():
    b15, b1 = setup15(); d = strategy.donchian_latest(b15, b1, P15)
    assert d["side"] == 1 and d["diag"]["tf"] == "15m" and d["diag"]["trend_tf"] == "1h" and "20x15m high" in d["reason"]
    assert d["stop_dist"] == pytest.approx(3.0 * d["atr"])
    b15, b1 = setup15(up=False); assert strategy.donchian_latest(b15, b1, P15)["side"] == 0

def test_15m_short_only_when_enabled():
    b15, b1 = setup15(up=False, breakout=False, down_break=True)
    assert strategy.donchian_latest(b15, b1, P15)["side"] == 0               # long-only default
    d = strategy.donchian_latest(b15, b1, {**P15, "long_only": False})
    assert d["side"] == -1 and "SHORT breakdown" in d["reason"]

def test_1h_trend_bar_not_used_before_close():
    b15, b1 = setup15(); t_close = b15[-1][0] + 900
    extra = (b1[-1][0] + 3600, 0, 0, 0, 1.0, 0)
    if extra[0] + 3600 > t_close:
        assert strategy.donchian_latest(b15, b1 + [extra], P15)["diag"]["close_trend"] == b1[-1][4]

def test_signal_matches_research_simulator():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))
    import swing, donchian15
    b15, b1 = setup15(); fund = []
    S = swing.Series(b15, "15m", fund); donchian15.add_htf(S, b15, "1h", 3600)
    p = {"strat": "trend", "n": 20, "htf": "1h", "k": 3.0, "m": 3.0, "long_only": True}
    b1r = swing.resample(b15, 3600)
    for i in range(len(b15) - 40, len(b15)):
        r = swing.sig_trend(S, i, p); w1 = [b for b in b1r if b[0] + 3600 <= b15[i][0] + 900]
        d = strategy.donchian_latest(b15[:i + 1], w1, P15)
        assert (r[0] if r else 0) == d["side"]

def test_engine_15m_warmup_entry_trail_and_report():
    b15, b1 = setup15(); f = Feed15(b15, b1); e = engine(f, "donchian_15m")
    assert e.entry_sec() == 900
    asyncio.run(e.evaluate("BTC", warmup=True))
    assert ("15m", 600) in f.asked and ("1h", 300) in f.asked
    assert e.signals["BTC"]["side"] == 1 and "fresh 15m close" in e.signals["BTC"]["action"] and not e.pf.positions
    asyncio.run(e.evaluate("BTC"))
    pos = e.pf.positions["BTC"]; assert pos.mode == "trend" and pos.setup == "donchian_15m" and pos.lev <= 3
    info = e.strategy_info(); assert info["tf"] == "15m" and info["trend_tf"] == "1h" and info["experimental"]
    r = e.report(); assert r["expect"]["pf"] == 0.74 and "too early" in r["verdict"]
    t = e.report_text(); assert "Donchian-20 15m" in t and "net %" in t and "-85.77" in t

def test_report_filters_by_strategy_tag():
    b15, b1 = setup15(); e = engine(Feed15(b15, b1), "donchian_15m"); now = time.time()
    base = dict(symbol="BTC", side=1, entry=1, exit=1, qty=1, fees=0.1, funding=0, r_mult=1.0, opened=now - 50, closed=now - 10, manual=0, lev=3, notional=1, reason="x")
    e.store.trade({**base, "id": "a", "pnl": 5.0, "setup": "donchian_15m"}); e.store.trade({**base, "id": "b", "pnl": -3.0, "setup": "donchian"})
    e.store.trade({**base, "id": "c", "pnl": -1.0, "setup": "donchian_15m"})
    since = e.store.get("strategy_since"); since["donchian_15m"] = now - 100; e.store.put("strategy_since", since)
    L = e.report()["live"]; assert L["trades"] == 2 and L["net"] == 4.0 and L["pf"] == 5.0

def at(y, m, d, hh, mm): return dt.datetime(y, m, d, hh, mm, tzinfo=IST).timestamp()

def test_auto_report_every_2_days_at_20_ist():
    b15, b1 = setup15(); e = engine(Feed15(b15, b1), "donchian_15m")
    assert e.cfg["report"]["every_days"] == 2 and e.cfg["report"]["at_ist"] == "20:00"
    assert e.report_due(at(2026, 11, 3, 21, 0)) is None                    # odd day
    assert e.report_due(at(2026, 11, 4, 19, 59)) is None                   # too early
    assert e.report_due(at(2026, 11, 4, 20, 0)) == "2026-11-04"
    assert e.next_report_at(at(2026, 11, 3, 10, 0)) == at(2026, 11, 4, 20, 0)
    assert e.next_report_at(at(2026, 11, 2, 22, 49)) == at(2026, 11, 2, 22, 49)  # anchor day, 20:00 passed, not sent -> due now
    e.store.put("auto_report_last", "2026-11-04")
    assert e.report_due(at(2026, 11, 4, 23, 0)) is None                    # never twice a day
    assert e.next_report_at(at(2026, 11, 4, 20, 1)) == at(2026, 11, 6, 20, 0)
    e.cfg["report"]["every_days"] = 3; e.cfg["report"]["at_ist"] = "09:30"   # configurable
    assert e.report_due(at(2026, 11, 5, 9, 31)) == "2026-11-05" and e.report_due(at(2026, 11, 6, 9, 31)) is None
    e.cfg["report"]["every_days"] = 0; assert e.report_due(at(2026, 11, 5, 9, 31)) is None and e.next_report_at() is None

def test_auto_report_loop_sends_once(monkeypatch):
    b15, b1 = setup15(); e = engine(Feed15(b15, b1), "donchian_15m"); sent = []
    e.notify = sent.append; e.report_due = lambda now=None: None if e.store.get("auto_report_last") else "2026-11-04"
    calls = {"n": 0}
    async def fake_sleep(s):
        calls["n"] += 1
        if calls["n"] >= 3: raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError): asyncio.run(e.report_loop())
    assert len(sent) == 1 and "Auto report" in sent[0] and "Open positions" in sent[0] and "Strategy report" in sent[0]

def test_set_report_keys():
    c = copy.deepcopy(config.load())
    assert config.set_key(c, "report.every_days", "3") == 3 and config.set_key(c, "report.at_ist", "9:5") == "09:05"
    with pytest.raises(ValueError): config.set_key(c, "report.at_ist", "25:00")
    with pytest.raises(ValueError): config.set_key(c, "defaults_rev", "1")

def test_old_live_config_switched_to_new_default_once(tmp_path):
    old = copy.deepcopy(config.load(config.DEFAULT)); old.pop("defaults_rev"); old["strategy"]["name"] = "donchian_1h"; old["risk"]["risk_pct"] = 0.5
    p = tmp_path / "config.yaml"; p.write_text(yaml.safe_dump(old))
    c = config.load(str(p)); assert c["strategy"]["name"] == "gmgn_meme" and c["risk"]["risk_pct"] == 0.5 and c["defaults_rev"] == 3
    c["strategy"]["name"] = "donchian_1h"; config.save(c, str(p))        # owner switches back -> sticks
    assert config.load(str(p))["strategy"]["name"] == "donchian_1h"

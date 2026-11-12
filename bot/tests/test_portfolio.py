import copy, pytest
from futbot import config, portfolio
from futbot.execution import PaperExecutor, LiveExecutorDisabled, make
RULES = {"BTC": {"tickSize": 0.01, "sizeIncrement": 1e-8, "minNotional": 1, "maxLeverage": 40, "status": "TRADING"},
         "ETH": {"tickSize": 0.001, "sizeIncrement": 1e-4, "minNotional": 50, "maxLeverage": 25, "status": "TRADING"},
         "LOW": {"tickSize": 0.01, "sizeIncrement": 0.01, "minNotional": 10, "maxLeverage": 2, "status": "TRADING"}}

def pf(**over):
    c = copy.deepcopy(config.load())
    for k, v in over.items():
        sec, key = k.split("__"); c[sec][key] = v
    p = portfolio.Portfolio(c); p.rules = dict(RULES); return p

def test_fees_are_bulk_tier1():
    ex = PaperExecutor(config.load()["fees"]); assert ex.taker == pytest.approx(0.00035) and ex.maker_fee == 0

def test_leverage_capped_at_3_and_by_exchange():
    p = pf(risk__leverage=10); assert p.lev("BTC") == 3.0 and p.lev("LOW") == 2.0
    assert config.load()["risk"]["leverage"] <= 3

def test_sizing_risk_and_cap():
    p = pf(); price, sd = 50000.0, 500.0
    qty, capped = p.size(price, sd, 1000, symbol="BTC")
    loss = qty * (sd + 2 * p.ex.taker * price)
    assert not capped and loss == pytest.approx(10.0, rel=1e-3)                # 1% of 1000
    qty2, capped2 = p.size(price, 1.0, 1000, symbol="BTC")                     # tiny stop -> notional cap
    assert capped2 and qty2 * price <= 1000 * 3 / 2 + 1e-6

def test_min_notional_and_rounding():
    p = pf()
    assert p.open("ETH", 1, 2000.0, 20.0, 0, qty=0.02) is None                 # $40 < minNotional 50
    pos = p.open("ETH", 1, 2000.0, 20.0, 0, qty=0.12345)
    assert pos and pos.qty == pytest.approx(0.1234) and round(pos.stop / 0.001, 6) == round(pos.stop / 0.001)

def test_liq_price_and_slots():
    assert portfolio.liq_price(100, 1, 3, 0.005) == pytest.approx(67.1667, rel=1e-4)
    assert portfolio.liq_price(100, -1, 3, 0.005) == pytest.approx(132.8333, rel=1e-4)
    p = pf(); assert p.open("BTC", 1, 50000, 150, 0)
    ok, why = p.can_open("BTC", 1); assert not ok and "already" in why
    assert p.open("ETH", -1, 2000, 10, 0); ok, why = p.can_open("LOW", 1); assert not ok and "max 2" in why
    assert p.can_open("LOW", 1, manual=True)[0]

def test_tp1_maker_breakeven_and_trail_long():
    p = pf(); pos = p.open("BTC", 1, 50000, 150, 0, atr=60); r = pos.r; bal0 = p.balance
    p.on_price("BTC", pos.tp1 + 1, 60)
    assert pos.tp1_done and pos.qty == pytest.approx(pos.qty0 / 2, rel=1e-6) and pos.stop >= pos.entry
    p.on_price("BTC", pos.entry + 3 * r, 120); assert pos.stop > pos.entry + r  # trailing
    ev = p.on_price("BTC", pos.stop - 1, 180)
    t = p.closed[-1]; assert t["reason"] == "trailing stop" and t["pnl"] > 0 and t["tp1"]

def test_short_stop_loss_is_about_minus_1R_and_breaker():
    p = pf(); eq = p.balance
    pos = p.open("BTC", -1, 50000, 500, 0); p.on_price("BTC", pos.stop + 0.01, 10)
    assert eq - p.balance == pytest.approx(10.0, rel=0.05)                       # 1% risk incl. fees (slippage floor on top)
    for i in range(1, 4):
        pos = p.open("BTC", -1, 50000, 150, i * 100); p.on_price("BTC", pos.stop + 0.01, i * 100 + 10)
        assert p.closed[-1]["reason"] == "stop loss" and -1.45 < p.closed[-1]["r_mult"] < -1.0     # R measured on price; fees+slippage add ~0.3R on a 0.3% stop
    assert p.breakers.tripped and "consecutive" in p.breakers.tripped
    assert not p.can_open("ETH", 1000)[0]

def test_daily_cap_and_day_roll():
    p = pf(); p.breakers.roll(0, 1000); assert p.breakers.check_daily(969) and "daily" in p.breakers.tripped
    p.breakers.roll(86400 * 2, 969); assert not p.breakers.tripped

def test_close_fraction_and_remainder_rule():
    p = pf(); pos = p.open("ETH", 1, 2000, 20, 0, qty=0.06)                    # $120
    p.close("ETH", 0.5, 2000, 1, "manual")                                      # would leave $60 -> ok
    assert "ETH" in p.positions and p.positions["ETH"].qty == pytest.approx(0.03)
    p.close("ETH", 0.25, 2000, 2, "manual")                                     # would leave $45 < 50 -> closes all
    assert "ETH" not in p.positions

def test_funding_hourly_sign():
    p = pf(); p.open("BTC", 1, 50000, 150, 0); b = p.balance
    p.apply_funding("BTC", 0.0001, 50000, 3600); assert p.balance < b
    p2 = pf(); p2.open("BTC", -1, 50000, 150, 0); b2 = p2.balance
    p2.apply_funding("BTC", 0.0001, 50000, 3600); assert p2.balance > b2

def test_backtest_bar_gap_through_stop_and_max_hold():
    p = pf(); pos = p.open("BTC", 1, 50000, 150, 0)
    p.on_bar("BTC", pos.stop - 100, pos.stop - 50, pos.stop - 200, pos.stop - 80, 60)
    assert p.closed[-1]["exit"] < pos.stop                                      # filled at gap open, not at stop
    pos = p.open("ETH", 1, 2000, 20, 0, qty=0.1)
    p.on_price("ETH", 2000.5, 61 * 60); assert p.closed[-1]["reason"].startswith("max hold")

def test_live_executor_is_disabled():
    with pytest.raises(RuntimeError): LiveExecutorDisabled().market("BTC", "buy", 1, 1)
    with pytest.raises(RuntimeError): make({"mode": "live"})

def test_book_slippage_used_when_fresh():
    import time
    from futbot.feeds import book_slippage
    asks = [(100.0, 1.0), (101.0, 1.0)]
    assert book_slippage(asks, 50, 100) == 0 and book_slippage(asks, 1000, 100) is None
    s = book_slippage(asks, 150, 100); assert 0.003 < s < 0.004
    ex = PaperExecutor(config.load()["fees"]); ex.books["X"] = ([(99.0, 5)], asks, time.time())
    assert ex.slippage("X", "buy", 150, 100) == pytest.approx(s) and ex.slippage("X", "buy", 1000, 100) == 0.005
    assert ex.slippage("Y", "buy", 150, 100) == pytest.approx(0.0001)

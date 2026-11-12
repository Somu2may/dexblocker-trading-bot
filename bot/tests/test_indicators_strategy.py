import math, random
from futbot import indicators as ind, strategy, config

def test_ema_rsi_atr_basic():
    xs = [float(i) for i in range(1, 101)]
    e = ind.ema(xs, 10); assert abs(e[-1] - 95.5) < 0.6
    assert ind.rsi(xs, 14)[-1] > 99
    assert abs(ind.atr([x + 1 for x in xs], [x - 1 for x in xs], xs, 14)[-1] - 2.0) < 0.05

def test_resample_complete_buckets_only():
    bars = [(i * 60, 1, 2, 0.5, 1.5, 10) for i in range(0, 14)]       # 14 x 1m -> two full 5m bars, one partial dropped
    out = ind.resample(bars, 300)
    assert [b[0] for b in out] == [0, 300] and out[0][5] == 50 and out[0][2] == 2 and out[0][3] == 0.5

def _trend_bars(n, step, sec, start=100.0, noise=0.0, seed=1, t0=0):
    rnd = random.Random(seed); out = []; p = start
    for i in range(n):
        o = p; p = p + step + rnd.uniform(-noise, noise); out.append((t0 + i * sec, o, max(o, p) + 0.05, min(o, p) - 0.05, p, 1000.0))
    return out

def test_trend_filter_up_and_down():
    s = config.load()["strategy"]
    up = strategy.Features(_trend_bars(400, 0.01, 300), _trend_bars(500, 0.05, 900, t0=-300 * 900), s)
    dn = strategy.Features(_trend_bars(400, -0.01, 300, 500), _trend_bars(500, -0.05, 900, 500, t0=-300 * 900), s)
    assert up.trend(399)[0] == 1 and dn.trend(399)[0] == -1
    assert strategy.Features(_trend_bars(40, 0.01, 300), _trend_bars(40, 0.05, 900), s).trend(39)[0] == 0   # warm-up -> no trade

def test_decide_never_trades_without_trend_or_in_chop():
    s = config.load()["strategy"]
    flat = _trend_bars(400, 0.0, 300, noise=0.02); f = strategy.Features(flat, _trend_bars(500, 0.0, 900, noise=0.02, t0=-300 * 900), s)
    for i in range(250, 400): assert f.decide(i)["side"] == 0

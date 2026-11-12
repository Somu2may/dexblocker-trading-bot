"""15m Donchian variants vs the live 1h Donchian (same simulator/costs as swing.py).

  python bot/research/donchian15.py --data /path/with/{BTC,ETH,SOL}_15m.json [--bulk bot/data/backtest] --out report.md

Adds a 1h EMA50 trend filter (swing.py only has 4h/1d). Reports full-period stats, the 6 rolling 90-day windows and
a sanity check on BULK's own ~60 days of 1m candles (resampled to 15m) when --bulk is given.
"""
import argparse, itertools, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swing
from futbot import indicators as ind

def add_htf(S, bars15, htf, sec):
    hb = swing.resample(bars15, sec); hc = [x[4] for x in hb]; he = ind.ema(hc, 50); ht = [x[0] for x in hb]
    tr, j = [], -1
    for t in S.t:
        while j + 1 < len(ht) and ht[j + 1] + sec <= t + S.sec: j += 1
        tr.append(0 if j < 0 or he[j] is None else (1 if hc[j] > he[j] else -1))
    S.htf[htf] = tr

def build(data, tf):
    out = {}
    for s, (bars, fund) in data.items():
        S = swing.Series(bars, tf, fund); add_htf(S, bars, "1h", 3600); out[s] = S
    return out

def load_bulk(d):
    data = {}
    for s in swing.SYMS:
        k = json.load(open(os.path.join(d, f"bulk_{s}.json")))["k"]
        try: fund = json.load(open(os.path.join(d, f"ref_{s}.json"))).get("funding", [])
        except Exception: fund = []
        b1 = [(x[0], x[1], x[2], x[3], x[4], x[5]) for x in k]
        # resample 1m -> 15m (complete buckets only)
        out, cur, n = [], None, 0
        for b in b1:
            kk = b[0] // 900 * 900
            if cur is None or cur[0] != kk:
                if cur is not None and n == 15: out.append(tuple(cur))
                cur, n = [kk, b[1], b[2], b[3], b[4], b[5]], 1
            else:
                cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]; cur[5] += b[5]; n += 1
        data[s] = (out, [tuple(f) for f in fund])
    return data

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--bulk"); ap.add_argument("--out", default="donchian15.md")
    a = ap.parse_args(); swing._init(a.data); D = swing.DATA
    bars = D["BTC"][0]; T0 = bars[0][0] + 60 * 86400; T1 = bars[-1][0] + 900; W = swing.windows(T0, T1); days = (T1 - T0) / 86400
    ser = {"15m": build(D, "15m"), "1h": build(D, "1h")}
    L = []; P = L.append
    P(f"Data: Binance USDT-M 15m + funding {swing.day(bars[0][0])} -> {swing.day(T1)}, evaluated from {swing.day(T0)} ({days:.0f} d). "
      f"Costs: BULK taker 3.5 bps + 2 bps slippage, risk 1%, <=3x, max 2 open, 3% daily cap, 4-loss breaker.\n")
    P("| config | trades | /week | win% | PF | avg R | net % | max DD % | 90d windows (net %) |\n|---|---|---|---|---|---|---|---|---|")
    cfgs = [("LIVE 1h n=20 htf=4h k=3 m=3 long-only", {"strat": "trend", "tf": "1h", "n": 20, "htf": "4h", "k": 3.0, "m": 3.0, "long_only": True})]
    for n, htf, k, m, lo in itertools.product([20, 55], ["1h", "4h"], [2.0, 3.0], [3.0, 5.0], [True, False]):
        cfgs.append((f"15m n={n} htf={htf} k={k:g} m={m:g} {'long-only' if lo else 'long+short'}",
                     {"strat": "trend", "tf": "15m", "n": n, "htf": htf, "k": k, "m": m, "long_only": lo}))
    res = {}
    for name, p in cfgs:
        tr = swing.sel(swing.simulate(ser[p["tf"]], p), T0, T1); st = swing.stats(tr, days)
        ws = [swing.stats(swing.sel(tr, b, c), 90)["ret"] for _, b, c in W]; res[name] = (p, st, ws, tr)
        P(f"| {name} | {st['n']} | {st['wk']} | {st['win']} | {st['pf']} | {st['avg_r']} | {st['ret']} | {st['dd']} | {ws} |")
    if a.bulk:
        B = load_bulk(a.bulk); bt = B["BTC"][0]; b0, b1_ = bt[0][0] + 5 * 86400, bt[-1][0] + 900; bd = (b1_ - b0) / 86400
        bser = {"15m": build(B, "15m"), "1h": build(B, "1h")}
        P(f"\nBULK own candles {swing.day(bt[0][0])} -> {swing.day(b1_)} ({bd:.0f} d after 5 d warm-up; too short for 4h EMA50 on 1h - shown anyway):")
        P("| config | trades | /week | win% | PF | net % | max DD % |\n|---|---|---|---|---|---|---|")
        for name in [cfgs[0][0], "15m n=20 htf=1h k=3 m=3 long-only", "15m n=20 htf=1h k=3 m=3 long+short"]:
            p = res[name][0]; st = swing.stats(swing.sel(swing.simulate(bser[p["tf"]], p), b0, b1_), bd)
            P(f"| {name} | {st['n']} | {st['wk']} | {st['win']} | {st['pf']} | {st['ret']} | {st['dd']} |")
    open(a.out, "w").write("\n".join(L) + "\n"); print("\n".join(L))
    json.dump({k: {"p": v[0], "st": v[1], "ws": v[2]} for k, v in res.items()}, open(a.out + ".json", "w"))

if __name__ == "__main__": main()

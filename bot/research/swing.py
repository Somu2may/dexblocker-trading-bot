"""Swing / intraday strategy research on 15m and 1h bars (BTC, ETH, SOL) with strict rolling walk-forward.

Data: Binance USDT-M 15m klines + 8h funding (~2 years, free) - BULK itself only has ~60 days of history.
Costs/risk mirror the paper bot: BULK taker 3.5 bps (stops, trails, entries, time exits), maker 0 bps (limit targets that
trade THROUGH the level), 2 bps slippage on every taker fill, funding charged on open positions, 1% equity risk per trade
(incl. round-trip fees), notional <= equity * 3x / max_open, max 2 open, one per symbol, 3% daily loss cap and 4-loss
breaker (both reset next IST day). Signals on bar close, fills at next bar open, stop checked before target inside a bar.

  python bot/research/swing.py --data /path/with/{BTC,ETH,SOL}_15m.json --out report.md
"""
import argparse, bisect, datetime as dt, itertools, json, math, multiprocessing as mp, os, random, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from futbot import indicators as ind

SYMS = ["BTC", "ETH", "SOL"]
TF = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
COST = dict(taker=0.00035, maker=0.0, slip=0.0002, risk=0.01, lev=3.0, max_open=2, daily_cap=0.03, max_consec=4, start=1000.0)
IST = 19800

def resample(bars, sec):
    out, cur, n, need = [], None, 0, None
    for b in bars:
        k = b[0] // sec * sec
        if cur is None or cur[0] != k:
            if cur is not None and n == need: out.append(tuple(cur))
            cur, n = [k, b[1], b[2], b[3], b[4], b[5]], 1; need = sec // 900
        else:
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]; cur[5] += b[5]; n += 1
    if cur is not None and n == need: out.append(tuple(cur))
    return out

class Series:
    """Indicators for one symbol on one timeframe + higher-timeframe trend mapped to each bar (last CLOSED htf bar)."""
    def __init__(self, bars15, tf, funding):
        b = bars15 if tf == "15m" else resample(bars15, TF[tf]); self.sec = TF[tf]
        self.t = [x[0] for x in b]; self.o = [x[1] for x in b]; self.h = [x[2] for x in b]; self.l = [x[3] for x in b]; self.c = [x[4] for x in b]
        c, h, l = self.c, self.h, self.l
        self.ema20, self.ema50 = ind.ema(c, 20), ind.ema(c, 50)
        self.atr, self.adx, self.rsi = ind.atr(h, l, c, 14), ind.adx(h, l, c, 14), ind.rsi(c, 14)
        self.sma20 = ind.sma(c, 20)
        self.sd20 = [None] * len(c)
        for i in range(19, len(c)):
            m = self.sma20[i]; self.sd20[i] = math.sqrt(sum((x - m) ** 2 for x in c[i - 19:i + 1]) / 20)
        self.don = {}
        for n in (20, 55):
            hh, ll = ind.highest(h, n), ind.lowest(l, n)
            self.don[n] = ([None] + hh[:-1], [None] + ll[:-1])
        self.htf = {}
        for htf in ("4h", "1d"):
            hb = resample(bars15, TF[htf]); hc = [x[4] for x in hb]; he = ind.ema(hc, 50); ht = [x[0] for x in hb]
            tr, j = [], -1
            for t in self.t:
                while j + 1 < len(ht) and ht[j + 1] + TF[htf] <= t + self.sec: j += 1
                tr.append(0 if j < 0 or he[j] is None else (1 if hc[j] > he[j] else -1))
            self.htf[htf] = tr
        self.ft = [f[0] for f in funding]; self.fr = [f[1] for f in funding]

# ---------------- signals: return (side, stop_dist, mode, extra) or None, evaluated on CLOSE of bar i ----------------
def sig_trend(S, i, p):
    a = S.atr[i]; hh, ll = S.don[p["n"]][0][i], S.don[p["n"]][1][i]
    if a is None or hh is None: return None
    tr = S.htf[p["htf"]][i]
    if S.c[i] > hh and tr > 0: return (1, p["k"] * a, "trend", None)
    if S.c[i] < ll and tr < 0 and not p["long_only"]: return (-1, p["k"] * a, "trend", None)
    return None

def sig_pullback(S, i, p):
    a, e20, e50 = S.atr[i], S.ema20[i], S.ema50[i]
    if a is None or e50 is None or i < 4 or S.rsi[i - 3] is None: return None
    tr = S.htf[p["htf"]][i]; rmin = min(S.rsi[i - 3:i]); rmax = max(S.rsi[i - 3:i])
    if e20 > e50 and tr > 0 and rmin < p["rsi"] and S.c[i] > e20 and S.c[i] > S.o[i]: return (1, p["k"] * a, "pullback", None)
    if e20 < e50 and tr < 0 and rmax > 100 - p["rsi"] and S.c[i] < e20 and S.c[i] < S.o[i] and not p["long_only"]: return (-1, p["k"] * a, "pullback", None)
    return None

def sig_mr(S, i, p):
    a, ad, m, sd, r = S.atr[i], S.adx[i], S.sma20[i], S.sd20[i], S.rsi[i]
    if a is None or ad is None or sd is None or r is None or ad >= p["adx"]: return None
    if S.c[i] < m - p["bb"] * sd and r < p["rsi"]: return (1, p["k"] * a, "mr", None)
    if S.c[i] > m + p["bb"] * sd and r > 100 - p["rsi"] and not p["long_only"]: return (-1, p["k"] * a, "mr", None)
    return None

def sig_regime(S, i, p):
    ad = S.adx[i]
    if ad is None: return None
    if ad >= 25: return sig_trend(S, i, {**p, "k": p["tk"]})
    if ad < 20: return sig_mr(S, i, {**p, "k": p["mk"], "adx": 20})
    return None

SIG = {"trend": sig_trend, "pullback": sig_pullback, "mr": sig_mr, "regime": sig_regime}

# ---------------- portfolio simulation ----------------
def simulate(series, p, entries=None):
    """series: {sym: Series} same tf. entries: optional precomputed {sym: {i: (side, stop_dist, mode)}} (random baseline)."""
    C = COST; bal = C["start"]; pos = {}; trades = []; pend = []
    syms = [s for s in SYMS if s in series]; n = min(len(series[s].t) for s in syms)
    t0 = {s: series[s].t[-n] for s in syms}; off = {s: len(series[s].t) - n for s in syms}
    fidx = {s: bisect.bisect_right(series[s].ft, series[s].t[off[s]]) for s in syms}
    day, day_eq, consec, blocked = None, bal, 0, False
    sig = SIG[p["strat"]]
    def close(s, q, px, maker, ts, why):
        nonlocal bal, consec, blocked
        P = pos[s]; fee = q * px * (C["maker"] if maker else C["taker"])
        pnl = (px - P["entry"]) * q * P["side"] - fee; bal += pnl; P["pnl"] += pnl; P["fees"] += fee; P["qty"] -= q
        if P["qty"] <= P["q0"] * 1e-9:
            del pos[s]
            trades.append({"sym": s, "side": P["side"], "t_in": P["ts"], "t_out": ts, "pnl": P["pnl"], "r": P["pnl"] / P["risk"],
                           "f": P["pnl"] / P["eq"], "fees": P["fees"], "fund": P["fund"], "why": why, "mode": P["mode"]})
            consec = consec + 1 if P["pnl"] < 0 else 0
            if consec >= C["max_consec"]: blocked = True
    for k in range(n):
        ts = series[syms[0]].t[off[syms[0]] + k]
        d = (ts + IST) // 86400
        if d != day: day, day_eq, consec, blocked = d, bal, 0, False
        # entries from signals at the previous close
        for s, side, sd, mode in pend:
            S = series[s]; i = off[s] + k
            if s in pos or len(pos) >= C["max_open"] or blocked: continue
            px = S.o[i] * (1 + side * C["slip"]); risk = bal * C["risk"]
            q = min(risk / (sd + 2 * C["taker"] * px), bal * C["lev"] / C["max_open"] / px)
            if q * px < 10: continue
            fee = q * px * C["taker"]; bal -= fee
            pos[s] = {"side": side, "entry": px, "qty": q, "q0": q, "stop": px - side * sd, "r": sd, "mode": mode, "ts": ts, "bar": k,
                      "best": px, "tp1": (px + side * sd * p["tp1"]) if mode == "pullback" else None, "tp1_done": False,
                      "risk": q * (sd + 2 * C["taker"] * px), "eq": bal + fee, "pnl": -fee, "fees": fee, "fund": 0.0}
        pend = []
        # exits on this bar
        for s in list(pos):
            S = series[s]; i = off[s] + k; P = pos[s]; sd_ = P["side"]
            o, h, l, c = S.o[i], S.h[i], S.l[i], S.c[i]
            fi = fidx[s]                                             # funding settled inside this bar
            while fi < len(S.ft) and S.ft[fi] < ts + S.sec:
                if S.ft[fi] >= ts:
                    pay = sd_ * P["qty"] * o * S.fr[fi]; bal -= pay; P["pnl"] -= pay; P["fund"] -= pay
                fi += 1
            adverse, fav = (l, h) if sd_ > 0 else (h, l)
            if (o - P["stop"]) * sd_ <= 0: close(s, P["qty"], o * (1 - sd_ * C["slip"]), False, ts, "stop gap"); continue
            if (adverse - P["stop"]) * sd_ <= 0:
                why = "stop" if not (P["tp1_done"] or (P["stop"] - P["entry"]) * sd_ > 0) else "trail"
                close(s, P["qty"], P["stop"] * (1 - sd_ * C["slip"]), False, ts, why); continue
            if P["mode"] == "mr":
                tgt = S.sma20[i - 1]
                if tgt is not None and (fav - tgt) * sd_ > 0 and (tgt - P["entry"]) * sd_ > 0:
                    close(s, P["qty"], tgt, True, ts, "target"); continue
            if P["tp1"] is not None and not P["tp1_done"] and (fav - P["tp1"]) * sd_ > 0:
                close(s, P["qty"] * 0.5, P["tp1"], True, ts, "tp1"); P["tp1_done"] = True
                P["stop"] = P["entry"] * (1 + sd_ * 0.001) if sd_ > 0 else P["entry"] * (1 - 0.001)
            held = k - P["bar"] + 1
            if P["mode"] == "mr" and held >= p["hold"]: close(s, P["qty"], c * (1 - sd_ * C["slip"]), False, ts, "time"); continue
            if P["mode"] == "pullback" and held * S.sec >= 3 * 86400: close(s, P["qty"], c * (1 - sd_ * C["slip"]), False, ts, "time"); continue
            P["best"] = max(P["best"], h) if sd_ > 0 else min(P["best"], l)
            a = S.atr[i]
            if P["mode"] == "trend" or (P["mode"] == "pullback" and P["tp1_done"]):
                tm = p.get("m", 2.5); t = P["best"] - sd_ * tm * a
                if (t - P["stop"]) * sd_ > 0: P["stop"] = t
        for s in syms: fidx[s] = bisect.bisect_left(series[s].ft, ts + series[s].sec)
        if day_eq and (day_eq - bal) / day_eq >= C["daily_cap"]: blocked = True
        if k + 1 >= n: break
        for s in syms:
            if s in pos: continue
            i = off[s] + k
            if entries is not None:
                e = entries[s].get(i)
                if e: pend.append((s, *e))
                continue
            if p.get("syms") and s not in p["syms"]: continue
            r = sig(series[s], i, p)
            if r: pend.append((s, r[0], r[1], r[2]))
    for s in list(pos):                                              # mark open positions to the last close
        S = series[s]; close(s, pos[s]["qty"], S.c[-1], False, S.t[-1], "end")
    return trades

def stats(tr, days):
    if not tr: return {"n": 0, "wk": 0, "win": 0, "pf": 0, "avg_r": 0, "ret": 0, "dd": 0}
    tr = sorted(tr, key=lambda x: x["t_out"]); eq, peak, dd = 1.0, 1.0, 0.0
    for x in tr: eq *= 1 + x["f"]; peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
    g = sum(x["pnl"] for x in tr if x["pnl"] > 0); ls = -sum(x["pnl"] for x in tr if x["pnl"] < 0)
    return {"n": len(tr), "wk": round(len(tr) / days * 7, 2), "win": round(100 * sum(x["pnl"] > 0 for x in tr) / len(tr), 1),
            "pf": round(g / ls, 2) if ls else 99.0, "avg_r": round(sum(x["r"] for x in tr) / len(tr), 3), "ret": round((eq - 1) * 100, 2), "dd": round(dd * 100, 2)}

def grid():
    out = []
    for tf, n, htf, k, m, lo in itertools.product(["1h", "15m"], [20, 55], ["4h", "1d"], [2.0, 3.0], [3.0, 5.0], [False, True]):
        out.append({"strat": "trend", "tf": tf, "n": n, "htf": htf, "k": k, "m": m, "long_only": lo})
    for tf, rs, k, t1, lo in itertools.product(["1h", "15m"], [35, 45], [1.5, 2.5], [1.0, 2.0], [False, True]):
        out.append({"strat": "pullback", "tf": tf, "htf": "4h", "rsi": rs, "k": k, "tp1": t1, "m": 2.5, "long_only": lo})
    for tf, bb, k, hold, lo in itertools.product(["1h", "15m"], [2.0, 2.5], [1.5, 2.5], [12, 48], [False, True]):
        out.append({"strat": "mr", "tf": tf, "bb": bb, "rsi": 30, "adx": 20, "k": k, "hold": hold, "long_only": lo})
    for tf, n, lo in itertools.product(["1h", "15m"], [20, 55], [False, True]):
        out.append({"strat": "regime", "tf": tf, "n": n, "htf": "4h", "tk": 3.0, "m": 4.0, "bb": 2.0, "rsi": 30, "mk": 2.0, "hold": 24, "long_only": lo})
    return out

def key(p): return json.dumps(p, sort_keys=True)

DATA = {}
def _init(data_dir):
    global DATA
    for s in SYMS:
        j = json.load(open(os.path.join(data_dir, f"{s}_15m.json")))
        DATA[s] = (j["bars"], j["funding"])
_SER = {}
def series_for(tf):
    if tf not in _SER: _SER[tf] = {s: Series(DATA[s][0], tf, DATA[s][1]) for s in SYMS}
    return _SER[tf]
def run_cfg(p): return key(p), simulate(series_for(p["tf"]), p)

def windows(t_start, t_end, train=180, test=90):
    out, ts = [], t_start + train * 86400
    while ts + 20 * 86400 < t_end:
        out.append((ts - train * 86400, ts, min(ts + test * 86400, t_end))); ts += test * 86400
    return out

def sel(tr, a, b): return [x for x in tr if a <= x["t_in"] < b]
def day(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--out", default="swing_report.md")
    ap.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 1)); a = ap.parse_args()
    _init(a.data); G = grid(); t_go = time.time()
    with mp.Pool(a.procs, initializer=_init, initargs=(a.data,)) as pool:
        res = dict(pool.map(run_cfg, G, chunksize=2))
    print(f"{len(G)} configs simulated in {time.time() - t_go:.0f}s", flush=True)
    bars = DATA["BTC"][0]; T0 = bars[0][0] + 60 * 86400; T1 = bars[-1][0] + 900     # first 60d = indicator warm-up only
    W = windows(T0, T1); L = []
    P = lambda *x: L.append(" ".join(str(y) for y in x))
    hdr = "| | trades | /week | win% | PF | avg R | ret % | max DD % |\n|---|---|---|---|---|---|---|---|"
    row = lambda name, s: f"| {name} | {s['n']} | {s['wk']} | {s['win']} | {s['pf']} | {s['avg_r']} | {s['ret']} | {s['dd']} |"
    P(f"Data: Binance USDT-M 15m + funding, {day(bars[0][0])} -> {day(T1)}; evaluation from {day(T0)} (first 60 d = warm-up).")
    P(f"Walk-forward: train 180 d -> test 90 d, rolled every 90 d: {len(W)} out-of-sample windows "
      + ", ".join(f"{day(b)}..{day(c)}" for _, b, c in W) + ".")
    P(f"Costs: taker {COST['taker']*1e4:.1f} bps + {COST['slip']*1e4:.0f} bps slippage, maker {COST['maker']*1e4:.0f} bps, Binance funding; risk 1%/trade, <=3x, max 2 open.\n")
    oos_days = (W[-1][2] - W[0][1]) / 86400
    # ---- walk-forward per family and overall ----
    fams = ["trend", "pullback", "mr", "regime", "ALL", "trend_L", "pullback_L"]; chosen = {}; summary = {}
    for fam in fams:
        f0, lo = fam.split("_")[0], fam.endswith("_L")
        cands = [p for p in G if (f0 == "ALL" or p["strat"] == f0) and (not lo or p["long_only"])]; oos = []; picks = []
        for a_, b_, c_ in W:
            best, bs = None, -1e9
            for p in cands:
                st = stats(sel(res[key(p)], a_, b_), (b_ - a_) / 86400)
                sc = st["ret"] if st["n"] >= 15 else -1e9
                if sc > bs: best, bs = p, sc
            picks.append((best, bs)); oos += sel(res[key(best)], b_, c_)
        chosen[fam] = picks; summary[fam] = stats(oos, oos_days)
        summary[fam + "_win_windows"] = sum(stats(sel(res[key(pk)], b_, c_), 90)["ret"] > 0 for (pk, _), (_, b_, c_) in zip(picks, W))
    P("## 1) Strict walk-forward (params re-picked on each 180 d train window by train return, min 15 trades; stitched OOS)")
    P(hdr.replace("| |", "| family |", 1))
    for fam in fams: P(row(f"{fam} ({summary[fam + '_win_windows']}/{len(W)} OOS windows > 0)", summary[fam]))
    P("\nPicks per window:")
    for fam in fams:
        P(f"- **{fam}**: " + "; ".join(f"{day(W[i][1])}: {short(pk)} (train {bs:+.1f}%)" for i, (pk, bs) in enumerate(chosen[fam])))
    # ---- every config on the full period + rolling-window consistency ----
    full_days = (T1 - T0) / 86400; table = []
    for p in G:
        tr = sel(res[key(p)], T0, T1); st = stats(tr, full_days)
        wpos = sum(stats(sel(tr, b_, c_), 90)["ret"] > 0 for _, b_, c_ in W)
        table.append((p, st, wpos))
    table.sort(key=lambda x: -x[1]["ret"])
    P(f"\n## 2) Every config on the full evaluation period ({full_days:.0f} d) - top 15 by return, and per family counts")
    P("| config | trades | /week | win% | PF | avg R | ret % | max DD % | OOS windows > 0 |\n|---|---|---|---|---|---|---|---|---|")
    for p, st, wpos in table[:15]: P(row(short(p), st)[:-1] + f" {wpos}/{len(W)} |")
    for fam in ["trend", "pullback", "mr", "regime"]:
        fs = [x for x in table if x[0]["strat"] == fam]
        P(f"- {fam}: {sum(x[1]['ret'] > 0 for x in fs)}/{len(fs)} configs profitable over the full period; "
          f"{sum(x[1]['pf'] >= 1.2 and x[2] >= len(W) - 2 for x in fs)} with PF >= 1.2 and >= {len(W) - 2}/{len(W)} windows positive")
    # ---- robustness of the best full-period config: one-step neighbours ----
    best = table[0][0]; P(f"\n## 3) Neighbours of the best full-period config {short(best)}")
    P("| neighbour | trades | /week | win% | PF | avg R | ret % | max DD % |\n|---|---|---|---|---|---|---|---|")
    for p, st, wpos in table:
        diff = [k for k in p if p[k] != best.get(k)]
        if p["strat"] == best["strat"] and len(diff) == 1: P(row(f"{diff[0]}={p[diff[0]]}", st))
    # ---- per symbol for the best config and the walk-forward ALL picks ----
    P(f"\n## 4) Per symbol - best full-period config {short(best)}")
    P(hdr)
    trb = sel(res[key(best)], T0, T1)
    for s in SYMS: P(row(s, stats([x for x in trb if x["sym"] == s], full_days)))
    P(f"long/short: {sum(x['side'] > 0 for x in trb)}/{sum(x['side'] < 0 for x in trb)} · exits: " +
      str({w: sum(x['why'] == w for x in trb) for w in sorted(set(x['why'] for x in trb))}) +
      f" · fees ${sum(x['fees'] for x in trb):.0f}, funding ${sum(x['fund'] for x in trb):+.0f}")
    # ---- baselines ----
    P("\n## 5) Baselines over the same period")
    P("| baseline | ret % | max DD % |\n|---|---|---|")
    for s in SYMS:
        S = series_for("1h")[s]; i0 = bisect.bisect_left(S.t, T0); cs = S.c[i0:]; pk, dd = cs[0], 0
        for x in cs: pk = max(pk, x); dd = max(dd, 1 - x / pk)
        P(f"| buy & hold {s} (1x, no fees) | {round((cs[-1] / cs[0] - 1) * 100, 1)} | {round(dd * 100, 1)} |")
    P("\nRandom entries with the best config's exits (same tf, same number of signals per symbol, 20 seeds; longs only if config is long-only):")
    rs = []
    ser = series_for(best["tf"])
    for seed in range(20):
        rnd = random.Random(seed); ent = {}
        for s in SYMS:
            S = ser[s]; cnt = sum(1 for x in trb if x["sym"] == s) * 2
            idx = rnd.sample(range(300, len(S.t) - 1), min(cnt, len(S.t) - 301)); ent[s] = {}
            for i in idx:
                if S.atr[i]: ent[s][i] = (1 if best.get("long_only") or rnd.random() < 0.5 else -1, best.get("k", best.get("tk", 2.0)) * S.atr[i], best["strat"] if best["strat"] != "regime" else "trend")
        rs.append(stats(sel(simulate(ser, best, ent), T0, T1), full_days))
    rs.sort(key=lambda x: x["ret"])
    P(f"random median ret {rs[10]['ret']}% (PF {rs[10]['pf']}), best of 20 {rs[-1]['ret']}% (PF {rs[-1]['pf']}), worst {rs[0]['ret']}% · strategy {table[0][1]['ret']}%")
    open(a.out, "w").write("\n".join(L) + "\n"); print("\n".join(L))
    json.dump({"summary": summary, "best": best, "chosen": {f: [c[0] for c in v] for f, v in chosen.items()}}, open(a.out + ".json", "w"))

def short(p):
    s = {k: v for k, v in p.items() if k not in ("strat",)}
    return p["strat"] + " " + " ".join(f"{k}={'L' if v is True else 'LS' if v is False else v}" if k == "long_only" else f"{k}={v}" for k, v in s.items())

if __name__ == "__main__": main()

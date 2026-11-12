"""High-frequency (1m) paper-scalper research for BULK perps: maker-style mean reversion vs taker variants.

  python bot/research/hf.py --binance /workspace/futdata/m1 --funding /workspace/futdata/research --bulk bot/data/backtest --out hf.md

Data: Binance USDT-M 1m klines (monthly zips from data.binance.vision) + BULK's own ~60 days of 1m candles.
Cost / fill model (conservative on purpose):
  * taker fills: BULK taker 3.5 bps + `slip` bps slippage, at the NEXT bar open (signal on bar close)
  * maker fills: post-only (ALO) limit quote posted after bar i closes - only on the passive side of the close (a quote already
    through the market would be rejected by ALO), valid during bar i+1 only; fills at the limit price with 0 bps fee ONLY
    if the bar trades THROUGH the price by `thr` (touching is not a fill: queue position unknown)
  * after an entry fill inside a bar, the stop is checked against the rest of that bar's range (worst case), the target is not
  * stop = taker (+ slippage); target = maker limit (needs trade-through); time exit = taker at bar close
  * 1 position per symbol, max_open total, risk % of equity per trade (stop distance + round-trip taker fees),
    notional <= equity * 3 / max_open, daily loss cap (IST day), consecutive-loss breaker (rest of day / pause N min / off),
    funding charged on open positions.
"""
import argparse, bisect, csv, datetime as dt, io, itertools, json, math, multiprocessing as mp, os, sys, zipfile
import numpy as np
IST = 19800; SYMS = ["BTC", "ETH", "SOL"]

def load_binance(d):
    out = {}
    for s in SYMS:
        rows = []
        for f in sorted(x for x in os.listdir(d) if x.startswith(s + "USDT-1m-") and x.endswith(".zip")):
            with zipfile.ZipFile(os.path.join(d, f)) as z:
                for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                    if r[0].startswith("open"): continue
                    rows.append((int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])))
        rows.sort(); out[s] = np.array(rows)
    return out

def load_funding(d):
    f = {}
    for s in SYMS:
        try: f[s] = [tuple(x) for x in json.load(open(os.path.join(d, f"{s}_15m.json")))["funding"]]
        except Exception: f[s] = []
    return f

def load_bulk(d):
    out, fund = {}, {}
    for s in SYMS:
        k = json.load(open(os.path.join(d, f"bulk_{s}.json")))["k"]
        out[s] = np.array([(x[0], x[1], x[2], x[3], x[4], x[5]) for x in k])
        try: fund[s] = [tuple(x) for x in json.load(open(os.path.join(d, f"ref_{s}.json"))).get("funding", [])]
        except Exception: fund[s] = []
    return out, fund

# ---------------- indicators (numpy) ----------------
def ema(x, n):
    a = 2 / (n + 1); out = np.empty_like(x); out[0] = x[0]
    for i in range(1, len(x)): out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out
def atr(h, l, c, n=14):
    pc = np.concatenate([[c[0]], c[:-1]]); tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    out = np.empty_like(tr); out[0] = tr[0]; a = 1 / n
    for i in range(1, len(tr)): out[i] = out[i - 1] + a * (tr[i] - out[i - 1])
    return out
def roll_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full_like(x, np.nan); out[n:] = sw(x, n).max(axis=1)[:-1]; return out    # prior n bars, excl. current
def roll_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full_like(x, np.nan); out[n:] = sw(x, n).min(axis=1)[:-1]; return out
def resample_trend(t, c, sec, n):
    """For every 1m bar: +1/-1 if the last CLOSED `sec` bar close is above/below its EMA(n)."""
    k = (t // sec).astype(np.int64); last = np.r_[k[1:] != k[:-1], True]
    bt, bc = k[last], c[last]; be = ema(bc, n); sgn = np.sign(bc - be)
    # bar of bucket b closes at (b+1)*sec; a 1m bar closing at t+60 may use buckets with (b+1)*sec <= t+60
    idx = np.searchsorted((bt + 1) * sec, t + 60, side="right") - 1
    out = np.where(idx >= n, sgn[np.clip(idx, 0, None)], 0); return out

class Feat:
    def __init__(self, a, p):
        self.t = a[:, 0].astype(np.int64); self.o, self.h, self.l, self.c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        self.mean = ema(self.c, p["L"]); self.atr = atr(self.h, self.l, self.c, 14)
        self.atrp = self.atr / self.c * 100
        self.tr15 = resample_trend(self.t, self.c, 900, 50) if p.get("trend") else np.zeros(len(self.c))
        if p["strat"] in ("mom_taker", "mom_maker"): self.hh, self.ll = roll_max(self.h, p["n"]), roll_min(self.l, p["n"])

# ---------------- simulation ----------------
C0 = dict(taker=0.00035, maker=0.0, slip=0.0001, thr=0.0001, start=1000.0, lev=3.0)

def simulate(data, fund, p, C=None):
    C = {**C0, **(C or {})}; R = p.get("risk", 0.3) / 100; max_open = p.get("max_open", 3)
    F = {s: Feat(data[s], p) for s in SYMS}
    T = sorted(set().union(*[set(F[s].t.tolist()) for s in SYMS])); idx = {s: {int(t): i for i, t in enumerate(F[s].t)} for s in SYMS}
    fidx = {s: 0 for s in SYMS}; ft = {s: [x[0] for x in fund.get(s, [])] for s in SYMS}; fr = {s: [x[1] for x in fund.get(s, [])] for s in SYMS}
    bal = C["start"]; pos = {}; quotes = {}; trades = []; fees_paid = [0.0]; day = None; day_eq = bal; consec = 0; block_until = 0; blocked_day = None
    stats = {"daily_cap_days": 0, "streak_trips": 0, "maker_fills": 0, "quotes": 0}
    cb = p.get("consec", ("pause", 30)); max_c = p.get("max_consec", 4)
    def close(s, px, maker, ts, why):
        nonlocal bal, consec, block_until, blocked_day
        P = pos.pop(s); fee = P["q"] * px * (C["maker"] if maker else C["taker"]); fees_paid[0] += fee
        pnl = (px - P["e"]) * P["q"] * P["sd"] - fee; bal += pnl; tot = P["pnl"] + pnl
        trades.append((s, P["sd"], P["ts"], ts, tot, tot / P["risk"], why, P["fees"] + fee, P["mk"]))
        consec = consec + 1 if tot < 0 else 0
        if consec >= max_c and cb[0] != "off":
            stats["streak_trips"] += 1; consec = 0
            if cb[0] == "day": blocked_day = day
            else: block_until = ts + 60 * cb[1]
    def open_(s, sd, px, maker, ts, stop_d, tgt):
        nonlocal bal
        risk = bal * R; q = min(risk / (stop_d + 2 * C["taker"] * px), bal * C["lev"] / max_open / px)
        if q * px < 10: return
        fee = q * px * (C["maker"] if maker else C["taker"]); bal -= fee; fees_paid[0] += fee
        pos[s] = {"sd": sd, "e": px, "q": q, "stop": px - sd * stop_d, "tgt": tgt, "ts": ts, "risk": q * (stop_d + 2 * C["taker"] * px), "pnl": -fee, "fees": fee, "mk": maker, "n": 0}
    for ts in T:
        d = (ts + IST) // 86400
        if d != day: day, day_eq = d, bal + sum(0 for _ in pos)
        for s in SYMS:
            i = idx[s].get(ts)
            if i is None: continue
            f = F[s]; o, h, l, c = f.o[i], f.h[i], f.l[i], f.c[i]
            # funding
            while fidx[s] < len(ft[s]) and ft[s][fidx[s]] < ts + 60:
                if ft[s][fidx[s]] >= ts and s in pos:
                    P = pos[s]; pay = P["sd"] * P["q"] * o * fr[s][fidx[s]]; bal -= pay; P["pnl"] -= pay
                fidx[s] += 1
            # 1) resting quote from previous close -> maker entry fill (trade-through required)
            qd = quotes.pop(s, None); filled_now = False
            if qd and s not in pos and len(pos) < max_open:
                for sd, lvl, stop_d, tgt in qd:
                    if (sd > 0 and l < lvl * (1 - C["thr"])) or (sd < 0 and h > lvl * (1 + C["thr"])):
                        px = min(lvl, o) if sd > 0 else max(lvl, o)          # gapped open: fill at the better open price
                        open_(s, sd, px, True, ts, stop_d, tgt); filled_now = s in pos; stats["maker_fills"] += filled_now; break
            # 1b) pending taker entry from previous close
            P = pos.get(s)
            if P and P.get("pend_taker"): pass
            # 2) exits
            P = pos.get(s)
            if P:
                sd = P["sd"]; P["n"] += 1
                if not filled_now and (o - P["stop"]) * sd <= 0: close(s, o * (1 - sd * C["slip"]), False, ts, "stop gap")
                elif ((l if sd > 0 else h) - P["stop"]) * sd <= 0: close(s, P["stop"] * (1 - sd * C["slip"]), False, ts, "stop")
                elif not filled_now and P["tgt"] is not None and ((h > P["tgt"] * (1 + C["thr"])) if sd > 0 else (l < P["tgt"] * (1 - C["thr"]))):
                    close(s, P["tgt"], True, ts, "target")
                elif P["n"] >= p["H"]: close(s, c * (1 - sd * C["slip"]), False, ts, "time")
            # breakers
            eq = bal
            if day_eq and (day_eq - eq) / day_eq >= p.get("daily_cap", 0.03):
                if blocked_day != day: stats["daily_cap_days"] += 1
                blocked_day = day
            # 3) new signal on this close
            if s in pos or blocked_day == day or ts + 60 <= block_until: continue
            a = f.atr[i]; m = f.mean[i]
            if i < 300 or not (p["atr_min"] <= f.atrp[i] <= p["atr_max"]): continue
            tr = f.tr15[i]
            if p["strat"] == "mr_maker":
                q = []
                for sd in (1, -1):
                    if p.get("trend") == "with" and tr != sd: continue
                    lvl = m - sd * p["k"] * a
                    if (c - lvl) * sd <= 0: continue                         # ALO post-only: a quote through the market would be rejected
                    tgt = (m if p["tp"] == "mean" else lvl + sd * p["tp"] * a)
                    q.append((sd, lvl, p["s"] * a, tgt))
                if q: quotes[s] = q; stats["quotes"] += 1
            elif p["strat"] == "mr_taker":
                for sd in (1, -1):
                    if p.get("trend") == "with" and tr != sd: continue
                    if (c - (m - sd * p["k"] * a)) * sd < 0:                  # close beyond the band -> market at next open
                        quotes[s] = [(sd, None, p["s"] * a, None)]; break
            elif p["strat"] == "mom_maker":                               # breakout -> rest a maker limit on the first pullback
                if not np.isnan(f.hh[i]):
                    for sd, ok in ((1, c > f.hh[i]), (-1, c < f.ll[i])):
                        if ok and (not p.get("trend") or tr == sd):
                            lvl = c - sd * p["pb"] * a; quotes[s] = [(sd, lvl, p["s"] * a, lvl + sd * p["tp"] * a)]; break
            elif p["strat"] == "mom_taker":
                if not np.isnan(f.hh[i]):
                    for sd, ok in ((1, c > f.hh[i]), (-1, c < f.ll[i])):
                        if ok and (not p.get("trend") or tr == sd): quotes[s] = [(sd, None, p["s"] * a, None)]; break
        # taker entries placed at this close execute at the next bar open: handle at the start of next bar via 'lvl None'
        for s, qd in list(quotes.items()):
            if qd and qd[0][1] is None:
                i = idx[s].get(ts); j = i + 1 if i is not None else None; f = F[s]
                quotes.pop(s)
                if j is None or j >= len(f.t) or s in pos or len(pos) >= max_open: continue
                sd, _, stop_d, _ = qd[0]; px = f.o[j] * (1 + sd * C["slip"])
                tgt = px + sd * p["tp"] * f.atr[i] if p["tp"] != "mean" else f.mean[i]
                open_(s, sd, px, False, int(f.t[j]) - 60, stop_d, tgt)       # becomes active on bar j
    for s in list(pos): f = F[s]; close(s, f.c[-1], False, int(f.t[-1]), "end")
    return trades, fees_paid[0], stats, bal

def summarize(trades, fees, stats, bal, days, start=1000.0):
    if not trades: return {"n": 0, "per_day": 0, "win": 0, "pf": 0, "net": 0, "dd": 0, "fees": 0, "avg_r": 0}
    tr = sorted(trades, key=lambda x: x[3]); eq = start; pk = start; dd = 0
    for x in tr: eq += x[4]; pk = max(pk, eq); dd = max(dd, (pk - eq) / pk)
    g = sum(x[4] for x in tr if x[4] > 0); ls = -sum(x[4] for x in tr if x[4] < 0)
    return {"n": len(tr), "per_day": round(len(tr) / days, 1), "win": round(100 * sum(x[4] > 0 for x in tr) / len(tr), 1),
            "pf": round(g / ls, 2) if ls else 99, "net": round((bal / start - 1) * 100, 2), "dd": round(dd * 100, 2), "fees": round(fees, 2),
            "avg_r": round(sum(x[5] for x in tr) / len(tr), 3), "maker_pct": round(100 * sum(x[8] for x in tr) / len(tr), 1),
            "cap_days": stats["daily_cap_days"], "streaks": stats["streak_trips"],
            "exits": {w: sum(x[6] == w for x in tr) for w in ("target", "stop", "stop gap", "time")}}

def grid():
    G = []
    for k, s, tp, H, L, tr in itertools.product([1.5, 2.5], [1.5, 3.0], ["mean", 1.0], [30], [20, 60], [None, "with"]):
        G.append({"strat": "mr_maker", "k": k, "s": s, "tp": tp, "H": H, "L": L, "trend": tr, "atr_min": 0.03, "atr_max": 1.0})
    for k, s, tp in itertools.product([1.5, 2.5], [1.5, 3.0], ["mean", 1.0]):
        G.append({"strat": "mr_taker", "k": k, "s": s, "tp": tp, "H": 30, "L": 20, "trend": None, "atr_min": 0.03, "atr_max": 1.0})
    for n, s, tp, tr in itertools.product([20, 60], [1.5, 3.0], [1.5, 3.0], [None, "with"]):
        G.append({"strat": "mom_taker", "n": n, "s": s, "tp": tp, "H": 30, "L": 20, "trend": tr, "atr_min": 0.03, "atr_max": 1.0})
    return G

DATA = {}
def _init(binance, funding, bulk):
    global DATA
    if binance: DATA["binance"] = (load_binance(binance), load_funding(funding))
    if bulk: DATA["bulk"] = load_bulk(bulk)
def _run(args):
    name, p, C = args; d, f = DATA[name]
    days = (max(d[s][-1, 0] for s in SYMS) - min(d[s][0, 0] for s in SYMS)) / 86400
    tr, fees, st, bal = simulate(d, f, p, C); return name, p, C, summarize(tr, fees, st, bal, days), days

def label(p):
    return " ".join(f"{k}={v}" for k, v in p.items() if k not in ("atr_min", "atr_max"))

# ---------------- final report for the live default (strategy.hf in config.yaml) ----------------
LIVE = {"strat": "mr_maker", "k": 3.0, "s": 8.0, "tp": "mean", "H": 120, "L": 60, "trend": None, "atr_min": 0.03, "atr_max": 1.0,
        "risk": 0.25, "max_open": 3, "daily_cap": 0.03, "max_consec": 4, "consec": ("pause", 30)}
SCEN = [("live: maker entry+target (trade-through 1 bp), breakers ON (3% day cap, 4-loss -> 30 min pause)", {}, {}),
        ("same, breakers OFF (raw edge)", {"daily_cap": 9.0, "consec": ("off", 0)}, {}),
        ("same, 4-loss breaker = rest of day (old rule)", {"consec": ("day", 0)}, {}),
        ("pessimistic maker: trade-through 3 bp", {}, {"thr": 0.0003}),
        ("optimistic maker: any trade-through (0 bp)", {}, {"thr": 0.0}),
        ("TAKER entries+targets (3.5 bps + 1 bp slip each)", {}, {"maker": 0.00045, "thr": 0.0}),
        ("gross: zero fees, zero slippage, touch fills", {"daily_cap": 9.0, "consec": ("off", 0)}, {"taker": 0.0, "slip": 0.0, "thr": 0.0})]
def _final(a):
    name, i = a; label, po, co = SCEN[i]; d, f = DATA[name]
    days = (max(d[s][-1, 0] for s in SYMS) - min(d[s][0, 0] for s in SYMS)) / 86400
    tr, fees, st, bal = simulate(d, f, {**LIVE, **po}, co); return name, i, summarize(tr, fees, st, bal, days), days
def final(binance, funding, bulk, out):
    jobs = [(n, i) for n in ("binance", "bulk") for i in range(len(SCEN))]
    with mp.Pool(7, initializer=_init, initargs=(binance, funding, bulk)) as pool: res = pool.map(_final, jobs)
    L = [f"Live default `hf_maker`: {label(LIVE)}", "",
         "| data | scenario | trades/day | win % | PF | avg R | net % | max DD % | fees $ | days stopped by 3% cap | streak pauses |", "|" + "---|" * 11]
    for n, i, s, days in res:
        L.append(f"| {n} {days:.0f} d | {SCEN[i][0]} | {s['per_day']} | {s['win']} | {s['pf']} | {s['avg_r']} | {s['net']} | {s['dd']} | {s['fees']} | {s['cap_days']} | {s['streaks']} |")
    open(out, "w").write("\n".join(L) + "\n"); print("\n".join(L))
    json.dump(res, open(out + ".json", "w"), default=float)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--binance"); ap.add_argument("--funding"); ap.add_argument("--bulk"); ap.add_argument("--out", default="hf.md")
    ap.add_argument("--procs", type=int, default=7); ap.add_argument("--final", action="store_true"); a = ap.parse_args()
    if a.final: final(a.binance, a.funding, a.bulk, a.out); sys.exit()
    jobs = [(n, p, {}) for n in (["binance"] if a.binance else []) + (["bulk"] if a.bulk else []) for p in grid()]
    with mp.Pool(a.procs, initializer=_init, initargs=(a.binance, a.funding, a.bulk)) as pool: res = pool.map(_run, jobs, chunksize=1)
    json.dump([{"data": r[0], "p": r[1], "C": r[2], "st": r[3], "days": r[4]} for r in res], open(a.out + ".json", "w"), default=str)
    L = ["| data | config | trades/day | win% | PF | avg R | net % | max DD % | fees $ | maker % | cap days | streak trips |", "|" + "---|" * 12]
    for n, p, C, st, days in sorted(res, key=lambda r: (r[0], -r[3]["net"])):
        L.append(f"| {n} {days:.0f}d | {label(p)} | {st['per_day']} | {st['win']} | {st['pf']} | {st['avg_r']} | {st['net']} | {st['dd']} | {st['fees']} | {st.get('maker_pct')} | {st.get('cap_days')} | {st.get('streaks')} |")
    open(a.out, "w").write("\n".join(L) + "\n"); print("\n".join(L))


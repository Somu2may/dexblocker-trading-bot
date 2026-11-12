"""Event-driven backtest on 1m bars with the SAME strategy + portfolio code as live.
Signal on a closed entry-tf bar -> market entry at the NEXT 1m open (+slippage, taker fee). Exits on 1m bars, stop checked
before target within a bar (conservative). Funding applied at each historical funding timestamp."""
import copy, math, statistics
from .strategy import Features, TF_SEC
from .indicators import resample
from .portfolio import Portfolio, ist_day

def run(data, cfg, t0=None, t1=None, warm_bars=None):
    """data: {SYM: {"k": [[ts,o,h,l,c,v],...] 1m venue bars, "funding": [[ts, rate],...], optional "vol": {ts: ref_volume} (reference
    venue volume replaces k's volume for the volume filter, 0 when missing), optional "rules": BULK exchangeInfo}}.
    Trades only with signal time in [t0, t1)."""
    s = cfg["strategy"]; etf, htf = TF_SEC[s["entry_tf"]], TF_SEC[s["trend_tf"]]
    pf = Portfolio(cfg); syms = [x for x in cfg["enabled_symbols"] if x in data]
    feats, eidx, bars1, fund = {}, {}, {}, {}
    for sym in syms:
        k = [tuple(b[:6]) for b in data[sym]["k"]]
        if data[sym].get("vol") is not None:
            rv = data[sym]["vol"]; k = [b[:5] + (rv.get(b[0], 0.0),) for b in k]
        bars1[sym] = {b[0]: b for b in k}
        if data[sym].get("rules"): pf.rules[sym] = data[sym]["rules"]
        eb = resample(k, etf) if etf > 60 else k; hb = resample(k, htf)
        F = Features(eb, hb, s, etf, htf); feats[sym] = F
        eidx[sym] = {F.t[i] + etf: i for i in range(len(F.t))}          # close time -> index
        fund[sym] = {int(ts // 60 * 60): r for ts, r in data[sym].get("funding", [])}
    allts = sorted(set().union(*[set(b.keys()) for b in bars1.values()]))
    if t1 is None: t1 = allts[-1] + 60
    pending, cooldown, eq_curve, last_close = {}, {}, [], {}
    skip_h = set(s.get("skip_hours_utc") or [])
    for ts in allts:
        # 1) fill pending entries at this bar's open
        for sym, sig in list(pending.items()):
            b = bars1[sym].get(ts)
            if not b: continue
            ok, why = pf.can_open(sym, ts)
            if ok: pf.open(sym, sig["side"], b[1], sig["stop_dist"], ts, atr=sig["atr"], setup=sig["setup"], reason=sig["reason"],
                           equity=pf.equity(last_close))
            del pending[sym]
        # 2) funding + exits on this bar
        for sym in syms:
            b = bars1[sym].get(ts)
            if not b: continue
            r = fund[sym].get(ts)
            if r is not None and sym in pf.positions: pf.apply_funding(sym, r, b[1], ts)
            if sym in pf.positions:
                pf.on_bar(sym, b[1], b[2], b[3], b[4], ts + 59)
                if sym not in pf.positions: cooldown[sym] = ts + 60 + 60 * float(s.get("cooldown_min", 0))
            last_close[sym] = b[4]
        eq = pf.equity(last_close)
        pf.breakers.roll(ts, eq); pf.breakers.check_daily(eq)
        if ts % 3600 == 0: eq_curve.append((ts, eq))
        # 3) signals at entry-tf close
        close_t = ts + 60
        if close_t % etf == 0 and (t0 is None or close_t >= t0) and close_t < t1:
            if int(close_t // 3600 % 24) in skip_h: continue
            for sym in syms:
                i = eidx[sym].get(close_t)
                if i is None or sym in pf.positions or cooldown.get(sym, 0) > close_t: continue
                d = feats[sym].decide(i)
                if d["side"]: pending[sym] = d
    for sym in list(pf.positions): pf.close(sym, 1, last_close[sym], allts[-1] + 60, "end of test")
    eq_curve.append((allts[-1] + 60, pf.equity(last_close)))
    return pf, eq_curve

def stats(pf, eq_curve, start, days):
    T = pf.closed; n = len(T)
    if not n: return {"trades": 0, "net": 0, "ret_pct": 0, "win_pct": 0, "pf": 0, "avg_r": 0, "max_dd_pct": 0, "per_day": 0, "fees": 0}
    wins = [t["pnl"] for t in T if t["pnl"] > 0]; losses = [t["pnl"] for t in T if t["pnl"] <= 0]
    peak, dd = start, 0.0
    run_eq = start
    for t in sorted(T, key=lambda t: t["closed"]):
        run_eq += t["pnl"]; peak = max(peak, run_eq); dd = max(dd, (peak - run_eq) / peak)
    for _, e in eq_curve: peak = max(peak, e); dd = max(dd, (peak - e) / peak)
    net = sum(t["pnl"] for t in T)
    return {"trades": n, "net": round(net, 2), "ret_pct": round(net / start * 100, 2), "win_pct": round(len(wins) / n * 100, 1),
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else float("inf"),
            "avg_r": round(statistics.mean(t["r_mult"] for t in T), 3), "max_dd_pct": round(dd * 100, 2), "per_day": round(n / days, 2),
            "fees": round(sum(t["fees"] for t in T), 2), "funding": round(sum(t["funding"] for t in T), 2),
            "gross_before_fees": round(net + sum(t["fees"] for t in T), 2)}

def per_symbol(pf, start, days):
    out = {}
    for sym in sorted({t["symbol"] for t in pf.closed}):
        sub = copy.copy(pf); sub.closed = [t for t in pf.closed if t["symbol"] == sym]
        out[sym] = stats(sub, [], start, days)
    return out

"""Scalping signal logic shared 1:1 by the backtester and the live engine.

Inputs are CLOSED bars only: entry timeframe bars (default 5m) and trend timeframe bars (15m), tuples (ts_open,o,h,l,c,vol).
Rules (all thresholds in config `strategy:`):
  trend   : 15m close vs EMA50/EMA200 (long: close>EMA50>EMA200, short: mirror); optional session-VWAP agreement
  chop    : ADX(14) on entry tf >= adx_min and ATR% inside [atr_pct_min, atr_pct_max]
  setups  : pullback  - EMA9>EMA21 (long), a recent low tagged EMA21, trigger bar closes back above EMA9, RSI band, volume >= vol_mult x avg
            breakout  - close beyond the prior N-bar high/low with volume >= breakout_vol_mult x avg and RSI not extreme
  stop    : max(atr_stop_mult x ATR, min_stop_pct of price)
"""
from . import indicators as ind

TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

class Features:
    def __init__(self, ebars, hbars, s, etf_sec=300, htf_sec=900):
        self.s, self.etf, self.htf = s, etf_sec, htf_sec
        self.t = [b[0] for b in ebars]; o = [b[1] for b in ebars]; h = [b[2] for b in ebars]; l = [b[3] for b in ebars]
        c = [b[4] for b in ebars]; v = [b[5] for b in ebars]
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v
        self.ef, self.es = ind.ema(c, s["ema_fast"]), ind.ema(c, s["ema_slow"])
        self.rsi = ind.rsi(c, 14); self.atr = ind.atr(h, l, c, 14); self.adx = ind.adx(h, l, c, 14)
        self.vavg = ind.sma(v, s["vol_avg_bars"])
        self.vwap = ind.session_vwap(self.t, h, l, c, v)
        n = s["breakout_bars"]
        self.hh = [None] + ind.highest(h, n)[:-1]    # prior-N high (excluding current bar)
        self.ll = [None] + ind.lowest(l, n)[:-1]
        self.ht = [b[0] for b in hbars]; hc = [b[4] for b in hbars]
        self.hc, self.h50, self.h200 = hc, ind.ema(hc, s["trend_ema_fast"]), ind.ema(hc, s["trend_ema_slow"])
        # index of last CLOSED trend bar for each entry bar (trend bar close time <= entry bar close time)
        self.hidx, j = [], -1
        for t in self.t:
            while j + 1 < len(self.ht) and self.ht[j + 1] + htf_sec <= t + etf_sec: j += 1
            self.hidx.append(j)

    def trend(self, i):
        j = self.hidx[i]
        if j < 0 or self.h200[j] is None: return 0, "warming up (15m EMA200)"
        c, f, sl = self.hc[j], self.h50[j], self.h200[j]
        if c > f > sl: return 1, f"15m up (close>EMA{self.s['trend_ema_fast']}>EMA{self.s['trend_ema_slow']})"
        if c < f < sl: return -1, f"15m down (close<EMA{self.s['trend_ema_fast']}<EMA{self.s['trend_ema_slow']})"
        return 0, "15m no trend"

    def decide(self, i):
        """Returns dict(side=+1/-1/0, setup, reason, atr, stop_dist, price, diag)."""
        s = self.s; c = self.c[i]
        out = {"side": 0, "setup": None, "price": c, "atr": self.atr[i], "ts": self.t[i] + self.etf}
        if i < 2 or None in (self.ef[i], self.es[i], self.rsi[i], self.atr[i], self.adx[i], self.vavg[i]):
            out["reason"] = "warming up"; return out
        tr, why = self.trend(i)
        atrp = self.atr[i] / c * 100
        out["diag"] = {"trend": tr, "adx": round(self.adx[i], 1), "rsi": round(self.rsi[i], 1), "atr_pct": round(atrp, 3),
                       "vol_x": round(self.v[i] / self.vavg[i], 2) if self.vavg[i] else None, "vwap": self.vwap[i]}
        if tr == 0: out["reason"] = why; return out
        if self.adx[i] < s["adx_min"]: out["reason"] = f"chop: ADX {self.adx[i]:.0f} < {s['adx_min']}"; return out
        if atrp < s["atr_pct_min"]: out["reason"] = f"too quiet: ATR {atrp:.2f}% < {s['atr_pct_min']}%"; return out
        if atrp > s["atr_pct_max"]: out["reason"] = f"too wild: ATR {atrp:.2f}% > {s['atr_pct_max']}%"; return out
        if s.get("use_vwap") and (c - self.vwap[i]) * tr <= 0: out["reason"] = "wrong side of VWAP"; return out
        volx = self.v[i] / self.vavg[i] if self.vavg[i] else 0
        r = self.rsi[i]; setup = None
        if "pullback" in s["setups"]:
            lb = s["pullback_lookback"]; tol = s["pullback_tol_atr"] * self.atr[i]
            if tr > 0 and self.ef[i] > self.es[i] and c > self.ef[i] and c > self.o[i] \
               and any(self.l[k] <= self.es[k] + tol for k in range(max(0, i - lb + 1), i + 1) if self.es[k]) \
               and s["rsi_long_min"] <= r <= s["rsi_long_max"] and volx >= s["vol_mult"]:
                setup = "pullback"
            if tr < 0 and self.ef[i] < self.es[i] and c < self.ef[i] and c < self.o[i] \
               and any(self.h[k] >= self.es[k] - tol for k in range(max(0, i - lb + 1), i + 1) if self.es[k]) \
               and 100 - s["rsi_long_max"] <= r <= 100 - s["rsi_long_min"] and volx >= s["vol_mult"]:
                setup = "pullback"
        if not setup and "breakout" in s["setups"] and self.hh[i] is not None:
            if tr > 0 and c > self.hh[i] and volx >= s["breakout_vol_mult"] and r <= s["breakout_rsi_max"]: setup = "breakout"
            if tr < 0 and c < self.ll[i] and volx >= s["breakout_vol_mult"] and r >= 100 - s["breakout_rsi_max"]: setup = "breakout"
        if not setup:
            out["reason"] = f"{'long' if tr > 0 else 'short'} bias, waiting for setup (RSI {r:.0f}, vol {volx:.1f}x)"; return out
        stop = max(s["atr_stop_mult"] * self.atr[i], s["min_stop_pct"] / 100 * c)
        out.update(side=tr, setup=setup, stop_dist=stop,
                   reason=f"{'LONG' if tr > 0 else 'SHORT'} {setup}: {why}, ADX {self.adx[i]:.0f}, RSI {r:.0f}, vol {volx:.1f}x")
        return out

def evaluate_latest(ebars, hbars, s, etf="5m", htf="15m"):
    f = Features(ebars, hbars, s, TF_SEC[etf], TF_SEC[htf]); return f.decide(len(ebars) - 1) if ebars else {"side": 0, "reason": "no data"}


# ---------------------------------------------------------------------------------------------------------------------
# EXPERIMENTAL Donchian breakout family (research: bot/research/swing.py + donchian15.py).
#   donchian_1h : 1h close > prior 20 x 1h high, last closed 4h close > 4h EMA50, long-only, 3 ATR stop, 3 ATR chandelier trail.
#   donchian_15m: same rules one step faster: 15m close > prior 20 x 15m high, last closed 1h close > 1h EMA50 (shorts optional
#                 via long_only: false). Backtest: clearly NEGATIVE (PF 0.74) - run only as a paper experiment the owner asked for.
# Exits: stop + chandelier trail below the highest high since entry, ratcheted on each closed entry-tf bar. No TP, no max hold.
STRATEGIES = {"donchian_15m": "Donchian-20 15m breakout · EXPERIMENTAL", "donchian_1h": "Donchian-20 1h breakout · EXPERIMENTAL",
              "scalper": "5m pullback scalper (backtest: no edge)"}
DONCHIAN = {"donchian_1h": "donchian", "donchian_15m": "donchian_15m"}     # strategy name -> config block under `strategy:`
SETUP = {"donchian_1h": "donchian", "donchian_15m": "donchian_15m"}        # strategy name -> trade `setup` tag (keeps 1h history)

def is_donchian(name): return name in DONCHIAN
def donchian_cfg(strat_cfg, name=None):
    name = name or strat_cfg.get("name") or "donchian_15m"
    return strat_cfg.get(DONCHIAN.get(name, "donchian")) or {}
def hist_n(p, which):
    """Warm-up bar counts: history_entry / history_trend (old 1h keys history_1h / history_4h still accepted)."""
    return int(p.get(f"history_{which}") or p.get("history_1h" if which == "entry" else "history_4h") or (600 if which == "entry" else 300))
def chan_label(n, tf): return f"{n}h" if tf == "1h" else f"{n}x{tf}"
def summary(name, p):
    if not is_donchian(name): return "5m pullback scalper"
    tf, ttf = p.get("tf", "1h"), p.get("trend_tf", "4h")
    return (f"{tf} close > prior {chan_label(p.get('channel', 20), tf)} high, {ttf} close > EMA{p.get('trend_ema', 50)}"
            + (" · long-only" if p.get("long_only", True) else f" (shorts: {tf} close < {chan_label(p.get('channel', 20), tf)} low, {ttf} below EMA)")
            + f" · stop {p.get('stop_atr', 3)} ATR, trail {p.get('trail_atr', 3)} ATR")

def closed_only(bars, sec, now):
    return [b for b in bars if b[0] + sec <= now]

def donchian_latest(b1, b4, p, now=None):
    """b1 = closed entry-tf bars (p.tf, default 1h), b4 = closed trend-tf bars (p.trend_tf, default 4h), ts = open time.
    Signal on the CLOSE of the last entry bar. (Names b1/b4 kept from the 1h/4h original.)"""
    n, tl, al = int(p.get("channel", 20)), int(p.get("trend_ema", 50)), int(p.get("atr_len", 14))
    tf, ttf = p.get("tf", "1h"), p.get("trend_tf", "4h"); esec, tsec = TF_SEC[tf], TF_SEC[ttf]; cl = chan_label(n, tf)
    out = {"side": 0, "setup": "donchian", "reason": "", "atr": None, "stop_dist": None, "price": None, "diag": {}}
    if len(b1) < max(n + 1, al + 1) or len(b4) < tl:
        out["reason"] = f"warming up ({len(b1)} x {tf}, {len(b4)} x {ttf} bars)"; return out
    h = [b[2] for b in b1]; l = [b[3] for b in b1]; c = [b[4] for b in b1]
    atr = ind.atr(h, l, c, al)[-1]; dch = max(h[-n - 1:-1]); dcl = min(l[-n - 1:-1])
    i4 = len(b4) - 1                                   # last trend bar CLOSED at or before the entry bar close
    t_close = b1[-1][0] + esec
    while i4 >= 0 and b4[i4][0] + tsec > t_close: i4 -= 1
    e4 = ind.ema([b[4] for b in b4[:i4 + 1]], tl)[-1] if i4 >= tl - 1 else None
    c4 = b4[i4][4] if i4 >= 0 else None
    trend = 0 if e4 is None else (1 if c4 > e4 else -1)
    px = c[-1]; dist = (dch / px - 1) * 100; dist_lo = (px / dcl - 1) * 100
    out.update(atr=atr, price=px, stop_dist=float(p.get("stop_atr", 3.0)) * atr)
    out["diag"] = {"trend": trend, "dch_high": dch, "dch_low": dcl, "dist_pct": round(dist, 2), "dist_low_pct": round(dist_lo, 2),
                   "ema_trend": e4, "close_trend": c4, "ema4h": e4, "close4h": c4, "tf": tf, "trend_tf": ttf, "long_only": bool(p.get("long_only", True)),
                   "atr": atr, "atr_pct": round(atr / px * 100, 2), "bar": b1[-1][0], "channel": n}
    tt = f"{ttf} {'UP' if trend > 0 else 'DOWN' if trend < 0 else 'n/a'} (close {c4:,.2f} {'>' if trend > 0 else '<'} EMA{tl} {e4:,.2f})" if e4 else f"{ttf} trend n/a"
    shorts = not p.get("long_only", True)
    if px > dch and trend > 0:
        out["side"] = 1; out["reason"] = f"LONG breakout: {tf} close {px:,.2f} > {cl} high {dch:,.2f} · {tt}"
    elif px > dch:
        out["reason"] = f"breakout above {cl} high but {tt} -> no long"
    elif px < dcl and shorts and trend < 0:
        out["side"] = -1; out["reason"] = f"SHORT breakdown: {tf} close {px:,.2f} < {cl} low {dcl:,.2f} · {tt}"
    elif px < dcl and shorts:
        out["reason"] = f"breakdown below {cl} low but {tt} -> no short"
    elif shorts and trend < 0:
        out["reason"] = f"waiting (short bias): {dist_lo:.2f}% above {cl} low {dcl:,.2f} · {tt}"
    else:
        out["reason"] = f"waiting: {dist:.2f}% below {cl} high {dch:,.2f} · {tt}"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# EXPERIMENTAL high-frequency maker scalper `hf_maker` (research: bot/research/hf.py, LIVE config there).
# On every closed 1m bar (flat symbol): rest a maker BUY limit at EMA(L) - k*ATR and a maker SELL limit at EMA(L) + k*ATR,
# valid for the next minute only. A quote fills only if price trades THROUGH it (fill at the limit, maker fee 0 bps).
# Exit: maker limit at the EMA(L) of the signal bar (target, needs trade-through), taker stop s*ATR, taker time exit after H min.
# Backtest: loses money with every tested parameter set (see docs/BACKTEST_HF.md) - paper experiment only.
STRATEGIES["gmgn_meme"] = "Solana memecoin scanner · PAPER"
STRATEGIES["hf_maker"] = "HF maker scalper 1m · EXPERIMENT"
SETUP["hf_maker"] = "hf_maker"

def _ema_seq(x, n):
    a = 2 / (n + 1); out = [x[0]]
    for v in x[1:]: out.append(out[-1] + a * (v - out[-1]))
    return out

def _atr_seq(h, l, c, n=14):
    out = []; prev = c[0]
    for i in range(len(c)):
        tr = max(h[i] - l[i], abs(h[i] - prev), abs(l[i] - prev)) if i else h[i] - l[i]; prev = c[i]
        out.append(tr if not out else out[-1] + (tr - out[-1]) / n)
    return out

def hf_quotes(bars, p):
    """bars = CLOSED 1m bars (ts, o, h, l, c, v). Same math as bot/research/hf.py (EMA seeded on the first bar, Wilder ATR
    seeded on the first true range, needs >= min_bars). Returns dict(quotes=[(side, level, stop_dist, target)], ...)."""
    L, k, s = int(p.get("ema", 60)), float(p.get("band_atr", 3.0)), float(p.get("stop_atr", 8.0))
    out = {"side": 0, "setup": "hf_maker", "quotes": [], "reason": "", "atr": None, "price": None, "stop_dist": None, "diag": {}}
    if len(bars) < int(p.get("min_bars", 301)):
        out["reason"] = f"warming up ({len(bars)} x 1m bars, need {p.get('min_bars', 301)})"; return out
    h = [b[2] for b in bars]; l = [b[3] for b in bars]; c = [b[4] for b in bars]
    m = _ema_seq(c, L)[-1]; a = _atr_seq(h, l, c, 14)[-1]; px = c[-1]; ap = a / px * 100
    out.update(price=px, atr=a, stop_dist=s * a)
    bid, ask = m - k * a, m + k * a
    out["diag"] = {"mean": m, "atr": a, "atr_pct": round(ap, 3), "bid": bid, "ask": ask, "bid_dist_pct": round((px / bid - 1) * 100, 3),
                   "ask_dist_pct": round((ask / px - 1) * 100, 3), "bar": bars[-1][0], "ema": L, "band_atr": k}
    if not float(p.get("atr_pct_min", 0.03)) <= ap <= float(p.get("atr_pct_max", 1.0)):
        out["reason"] = f"no quotes: 1m ATR {ap:.3f}% outside {p.get('atr_pct_min', 0.03)}-{p.get('atr_pct_max', 1.0)}%"; return out
    q = [x for x in ((1, bid, s * a, m), (-1, ask, s * a, m)) if (px - x[1]) * x[0] > 0]    # ALO post-only: passive side only
    out["quotes"] = q
    side_txt = lambda sd, lvl: f"{'BUY' if sd > 0 else 'SELL'} {lvl:,.2f} ({abs(px / lvl - 1) * 100:.2f}% {'below' if sd > 0 else 'above'})"
    out["reason"] = ("quoting: " + " / ".join(side_txt(sd, lvl) for sd, lvl, _, _ in q) if q else "no quotes: price already beyond the band (post-only)") \
                    + f" · EMA{L} {m:,.2f} · ATR {ap:.3f}%"
    return out

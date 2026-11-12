"""Plain-python indicator series (no numpy needed on the 1 GB VM). All return a list aligned with the input; None during warm-up."""
import math

def ema(xs, n):
    out, k, e = [None] * len(xs), 2 / (n + 1), None
    for i, x in enumerate(xs):
        if x is None: continue
        if e is None:
            if i + 1 >= n: e = sum(xs[i + 1 - n:i + 1]) / n; out[i] = e
            continue
        e = x * k + e * (1 - k); out[i] = e
    return out

def rma(xs, n):
    """Wilder smoothing (used by RSI/ATR/ADX)."""
    out, e, buf = [None] * len(xs), None, []
    for i, x in enumerate(xs):
        if x is None: continue
        if e is None:
            buf.append(x)
            if len(buf) == n: e = sum(buf) / n; out[i] = e
            continue
        e = (e * (n - 1) + x) / n; out[i] = e
    return out

def rsi(closes, n=14):
    g = [None] + [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    l = [None] + [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = rma(g, n), rma(l, n)
    return [None if a is None else (100.0 if b == 0 else 100 - 100 / (1 + a / b)) for a, b in zip(ag, al)]

def true_range(h, l, c):
    return [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]

def atr(h, l, c, n=14): return rma(true_range(h, l, c), n)

def adx(h, l, c, n=14):
    pdm = [None] + [(h[i] - h[i - 1]) if (h[i] - h[i - 1]) > (l[i - 1] - l[i]) and (h[i] - h[i - 1]) > 0 else 0.0 for i in range(1, len(c))]
    ndm = [None] + [(l[i - 1] - l[i]) if (l[i - 1] - l[i]) > (h[i] - h[i - 1]) and (l[i - 1] - l[i]) > 0 else 0.0 for i in range(1, len(c))]
    tr = [None] + true_range(h, l, c)[1:]
    str_, sp, sn = rma(tr, n), rma(pdm, n), rma(ndm, n)
    dx = []
    for t, p, m in zip(str_, sp, sn):
        if t is None or t == 0: dx.append(None); continue
        pdi, ndi = 100 * p / t, 100 * m / t
        dx.append(0.0 if pdi + ndi == 0 else 100 * abs(pdi - ndi) / (pdi + ndi))
    return rma(dx, n)

def sma(xs, n):
    out, s = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n: s -= xs[i - n]
        if i + 1 >= n: out[i] = s / n
    return out

def session_vwap(ts, h, l, c, v, reset_sec=86400):
    """VWAP that resets every UTC day (bar open time ts in seconds)."""
    out, pv, vv, day = [], 0.0, 0.0, None
    for t, hi, lo, cl, vol in zip(ts, h, l, c, v):
        d = int(t // reset_sec)
        if d != day: day, pv, vv = d, 0.0, 0.0
        pv += (hi + lo + cl) / 3 * vol; vv += vol
        out.append(pv / vv if vv else cl)
    return out

def highest(xs, n):
    return [None if i + 1 < n else max(xs[i + 1 - n:i + 1]) for i in range(len(xs))]

def lowest(xs, n):
    return [None if i + 1 < n else min(xs[i + 1 - n:i + 1]) for i in range(len(xs))]

def resample(bars, sec):
    """bars: list of (ts,o,h,l,c,v) 1m bars with ts = open time (s). Returns bars aggregated to `sec`, only COMPLETE buckets
    unless the last bucket ends exactly; partial last bucket is dropped."""
    out, cur = [], None
    for b in bars:
        k = int(b[0] // sec * sec)
        if cur is None or cur[0] != k:
            if cur is not None: out.append(tuple(cur))
            cur = [k, b[1], b[2], b[3], b[4], b[5], 1]
        else:
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]; cur[5] += b[5]; cur[6] += 1
    if cur is not None and cur[6] * 60 >= sec: out.append(tuple(cur))
    return [x[:6] for x in out]

"""Backtest CLI (same strategy + portfolio code as the live bot).
  python bot/backtest.py --days 60 [--source binance|okx] [--split 0.67] [--cache bot/data/backtest]
Downloads 1m candles + funding: the reference market (Binance USDT-M, or OKX swaps from US cloud IPs) AND BULK's own 1m candles
(GET /klines, 5000/request; BULK mainnet history only goes back ~60 days and its first weeks are noisy).
Reports: walk-forward (in-sample / out-of-sample / full) with BULK fees on the reference prices, per symbol, the same config on
BULK's own candles (reference volume for the volume filter), and a fee comparison BULK vs Hyperliquid vs all-taker."""
import argparse, json, os, sys, time, collections, datetime as dt
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import requests
from futbot import config, backtest

UA = {"User-Agent": "dexblocker-paper-bot/1.0"}
def _get(url, params):
    for i in range(8):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 200: return r.json()
            if r.status_code in (403, 451): raise SystemExit(f"{url} -> HTTP {r.status_code} (blocked from this IP, try --source okx)")
        except requests.RequestException: pass
        time.sleep(2 + i)
    raise SystemExit(f"download failed: {url}")

def download(sym, days, source):
    end = int(time.time() // 60 * 60) - 60; start = end - days * 86400; k, fr = [], []
    if source == "binance":
        t = start * 1000
        while t < end * 1000:
            r = _get("https://fapi.binance.com/fapi/v1/klines", {"symbol": sym + "USDT", "interval": "1m", "startTime": t, "limit": 1500})
            if not r: break
            k += [[x[0] // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])] for x in r]; t = r[-1][0] + 60000; time.sleep(0.1)
        t = start * 1000
        while True:
            r = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym + "USDT", "startTime": t, "limit": 1000})
            fr += [[x["fundingTime"] // 1000, float(x["fundingRate"])] for x in r]
            if len(r) < 1000: break
            t = r[-1]["fundingTime"] + 1
    else:  # okx (works from most cloud IPs)
        after = None
        while True:
            p = {"instId": f"{sym}-USDT-SWAP", "bar": "1m", "limit": 100}
            if after: p["after"] = after
            d = _get("https://www.okx.com/api/v5/market/history-candles", p)["data"]
            if not d: break
            k += [[int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])] for x in d if x[8] == "1"]
            after = d[-1][0]
            if int(after) // 1000 <= start: break
            time.sleep(0.11)
        k = sorted({x[0]: x for x in k if x[0] >= start}.values())
        after = None
        while True:
            p = {"instId": f"{sym}-USDT-SWAP", "limit": 100}
            if after: p["after"] = after
            d = _get("https://www.okx.com/api/v5/public/funding-rate-history", p)["data"]
            if not d: break
            fr += [[int(x["fundingTime"]) // 1000, float(x["realizedRate"] or x["fundingRate"])] for x in d]; after = d[-1]["fundingTime"]
            if int(after) // 1000 <= start: break
            time.sleep(0.2)
        fr = sorted(x for x in fr if x[0] >= start)
    return {"k": k, "funding": fr}

def download_bulk(sym, days):
    end = int(time.time() // 60 * 60) - 60; start = end - days * 86400; out = {}; t = start
    while t < end:
        e = min(end, t + 4999 * 60)
        r = _get("https://mainnet-api1.bulk.trade/api/v1/klines", {"symbol": f"{sym}-USD", "interval": "1m", "startTime": t * 1000, "endTime": e * 1000})
        for x in r: out[x["t"] // 1000] = [x["t"] // 1000, x["o"], x["h"], x["l"], x["c"], x["v"] * x["c"]]
        t = e; time.sleep(0.3)
    return {"k": [out[k] for k in sorted(out)]}

def table(rows):
    hdr = ["", "trades", "/day", "win%", "PF", "avg R", "net $", "ret %", "max DD %", "fees $"]
    out = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for name, s in rows:
        out.append(f"| {name} | {s['trades']} | {s['per_day']} | {s['win_pct']} | {s['pf']} | {s['avg_r']} | {s['net']} | {s['ret_pct']} | {s['max_dd_pct']} | {s['fees']} |")
    return "\n".join(out)

FEES = {"BULK tier1 (taker 3.5 / maker 0 bps)": {"taker_fee_pct": 0.035, "maker_fee_pct": 0.0},
        "Hyperliquid base (taker 4.5 / maker 1.5 bps)": {"taker_fee_pct": 0.045, "maker_fee_pct": 0.015},
        "BULK, TP1 as taker too": {"taker_fee_pct": 0.035, "maker_fee_pct": 0.035}}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=60); ap.add_argument("--source", default="binance")
    ap.add_argument("--split", type=float, default=0.67); ap.add_argument("--cache", default=os.path.join(D, "data", "backtest"))
    ap.add_argument("--config", default=None); a = ap.parse_args()
    cfg = config.load(a.config) if a.config else config.load(); os.makedirs(a.cache, exist_ok=True)
    try: rules = {x["baseAsset"]: x for x in requests.get("https://mainnet-api1.bulk.trade/api/v1/exchangeInfo", headers=UA, timeout=15).json()}
    except Exception: rules = {}
    syms = [s for s in cfg["enabled_symbols"] if not rules or rules.get(s, {}).get("status") == "TRADING"]
    ref, bulk = {}, {}
    for s in syms:
        for kind, fn, store in (("ref", lambda s=s: download(s, a.days, a.source), ref), ("bulk", lambda s=s: download_bulk(s, a.days), bulk)):
            f = os.path.join(a.cache, f"{kind}_{s}.json")
            if not os.path.exists(f) or time.time() - os.path.getmtime(f) > 6 * 3600:
                print("downloading", kind, s, flush=True); json.dump(fn(), open(f, "w"))
            store[s] = {**json.load(open(f)), "rules": rules.get(s)}
    for s in syms:   # BULK candles + reference volume + reference funding
        bulk[s]["vol"] = {x[0]: x[5] for x in ref[s]["k"]}; bulk[s]["funding"] = ref[s]["funding"]
    t0 = min(d["k"][0][0] for d in ref.values()); t1 = max(d["k"][-1][0] for d in ref.values()) + 60
    split = t0 + int((t1 - t0) * a.split) // 300 * 300; warm = 2.1
    days_is, days_oos = (split - t0) / 86400 - warm, (t1 - split) / 86400
    f = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    start = float(cfg["paper"]["start_balance"])
    print(f"symbols {syms} | data {f(t0)} -> {f(t1)} ({(t1 - t0) / 86400:.1f} d), in-sample until {f(split)}, out-of-sample after")
    print(f"\n## 1) Walk-forward on {a.source} 1m prices with BULK fees (taker {cfg['fees']['taker_fee_pct']}%, maker {cfg['fees']['maker_fee_pct']}%, slippage {cfg['fees']['slippage_pct']}%)")
    rows = []
    for name, lo, hi, days in [("in-sample", None, split, days_is), ("out-of-sample", split, None, days_oos), ("full", None, None, days_is + days_oos)]:
        pf, eq = backtest.run(ref, cfg, lo, hi); rows.append((name, backtest.stats(pf, eq, start, days)))
        if name == "full": full = pf
    print(table(rows))
    print("\nper symbol (full period)\n" + table([(s, st) for s, st in backtest.per_symbol(full, start, days_is + days_oos).items()]))
    print("exit reasons:", dict(collections.Counter(t["reason"] for t in full.closed)), "| longs/shorts:", sum(t["side"] > 0 for t in full.closed), "/", sum(t["side"] < 0 for t in full.closed))
    print("\n## 2) Same config on BULK's own 1m candles (prices = BULK trades; volume filter uses reference volume)")
    rows = []
    for name, lo, hi, days in [("BULK full", None, None, days_is + days_oos), ("BULK out-of-sample", split, None, days_oos)]:
        pf, eq = backtest.run(bulk, cfg, lo, hi); rows.append((name, backtest.stats(pf, eq, start, days)))
    print(table(rows))
    print("\n## 3) Fee comparison (full period, reference prices, same signals)")
    rows = []
    for name, fz in FEES.items():
        c = json.loads(json.dumps(cfg)); c["fees"].update(fz); pf, eq = backtest.run(ref, c); rows.append((name, backtest.stats(pf, eq, start, days_is + days_oos)))
    print(table(rows))

if __name__ == "__main__": main()

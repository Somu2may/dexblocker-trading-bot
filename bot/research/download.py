"""Download ~25 months of Binance USDT-M 15m klines + funding: python bot/research/download.py OUT_DIR [SYM ...]"""
import os
import httpx, json, time, sys, concurrent.futures as cf
END = int(time.time() // 900 * 900) * 1000; START = END - 2 * 365 * 86400 * 1000 - 30 * 86400 * 1000
def get(c, url, **p):
    for i in range(6):
        try:
            r = c.get(url, params=p, timeout=30); r.raise_for_status(); return r.json()
        except Exception as e: time.sleep(2 + i * 2); err = e
    raise err
def sym(s):
    c = httpx.Client(headers={"User-Agent": "research/1.0"}); out = {}; t = START
    while t < END:
        j = get(c, "https://fapi.binance.com/fapi/v1/klines", symbol=s + "USDT", interval="15m", startTime=t, limit=1500)
        if not j: break
        for k in j: out[k[0] // 1000] = [k[0] // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])]
        t = j[-1][0] + 900000
    f = {}; t = START
    while t < END:
        j = get(c, "https://fapi.binance.com/fapi/v1/fundingRate", symbol=s + "USDT", startTime=t, limit=1000)
        if not j: break
        for k in j: f[k["fundingTime"] // 1000] = float(k["fundingRate"])
        t = j[-1]["fundingTime"] + 1
        if len(j) < 1000: break
    bars = [out[k] for k in sorted(out) if k * 1000 + 900000 <= END]
    json.dump({"bars": bars, "funding": sorted(f.items())}, open(f"{s}_15m.json", "w"))
    return s, len(bars), len(f), bars[0][0], bars[-1][0]
if __name__ == "__main__":
    out_dir = sys.argv[1]; syms = sys.argv[2:] or ["BTC", "ETH", "SOL"]; os.makedirs(out_dir, exist_ok=True); os.chdir(out_dir)
    with cf.ThreadPoolExecutor(len(syms)) as ex:
        for r in ex.map(sym, syms): print(r, flush=True)

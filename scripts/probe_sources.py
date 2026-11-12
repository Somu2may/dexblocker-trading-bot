"""Prints which public market-data sources answer from this machine (run on the VM to prove cloud-IP reachability).
  python scripts/probe_sources.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
from futbot import config, feeds
async def main():
    cfg = config.load(); allsrc = list(dict.fromkeys(cfg["data"]["sources"] + cfg["data"]["volume_ref_sources"]))
    f = feeds.Feed(cfg, sources=allsrc); res = await f.probe()
    import httpx
    try:
        info = await f.srcs["bulk"].exchange_info()
        print("BULK exchangeInfo:", {s: info.get(s, {}).get("status", "MISSING") for s in cfg["symbols"]})
    except Exception as e: print("BULK exchangeInfo: error", e)
    try:                                     # warm-up history for the Donchian strategies (15m default, 1h selectable)
        b = f.srcs["bulk"]
        for tf, n in (("1m", 600), ("15m", 600), ("1h", 600), ("4h", 300)):
            k = await b.candles("BTC", tf, n); print(f"BULK klines BTC {tf}: {len(k)} bars (need {n} for instant warm-up)")
    except Exception as e: print("BULK klines: error", e)
    try:
        f.active = "bulk"; f.restart_ws(); await asyncio.sleep(6)
        print("BULK websocket:", "OK" if f.ws_ok and f.price else "no ticks", {s: f.price.get(s) for s in cfg["symbols"]})
    except Exception as e: print("BULK websocket: error", e)
    for n, r in res.items(): print(f"  {n:12s} {r}")
    await f.close()
asyncio.run(main())

"""Dry run against LIVE public market data WITHOUT Telegram (safe to run anywhere; never polls the bot token).
  python bot/dry_run.py --minutes 6 --data /tmp/futdry [--force-entry]
Probes the data sources, starts the paper engine + Mini App hooks, prints signals / positions every 30s.
--force-entry opens one small manual paper LONG on the first symbol after warm-up to exercise the position path."""
import argparse, asyncio, json, logging, os, sys, time
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); sys.path.insert(0, os.path.join(D, "..", "miniapp_app"))
from futbot import config
from futbot.store import Store
from futbot.engine import Engine

async def main(a):
    os.makedirs(a.data, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(a.data, "bot.log"))])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config.load()
    if cfg.get("strategy", {}).get("name") == "gmgn_meme":
        from futbot.meme_run import scan_and_print
        print("strategy is gmgn_meme — one public scan, no Telegram. Futures dry-run: /set strategy.name hf_maker in a futures config.")
        scan_and_print(cfg, limit=8)
        return
    msgs = []
    eng = Engine(cfg, Store(os.path.join(a.data, "futbot.db")), notify=lambda m: (msgs.append(m), print("\n[TELEGRAM WOULD SEND]\n" + m + "\n", flush=True)), data_dir=a.data)
    from miniapp import hooks
    hook = hooks.install(eng, cfg, a.data, config)
    tasks = await eng.start(); tasks.append(asyncio.create_task(hook()))
    print("sources:", json.dumps(eng.feed.status), "active:", eng.feed.active, flush=True)
    for s in cfg["enabled_symbols"]:                       # evaluate immediately instead of waiting for the next bar close
        try: await eng.evaluate(s, warmup=True)          # warm-up only: no entries on an old bar
        except Exception as e: print("evaluate", s, e)
    end = time.time() + a.minutes * 60; forced = False
    while time.time() < end:
        await asyncio.sleep(30)
        sn = eng.snapshot()
        print(time.strftime("%H:%M:%S"), "feed", sn["source"], "ws" if sn["ws"] else "rest", {s: (v["last"], v["age"]) for s, v in sn["prices"].items()},
              "| equity", round(sn["equity"], 2), "| breaker", sn["breaker"] or sn["stale"], flush=True)
        for s, d in sn["signals"].items(): print("   ", s, "->", d.get("reason"), "|", d.get("action"))
        for p in sn["positions"]: print("    POS", p["symbol"], p["side"], "entry", p["entry"], "mark", p["mark"], "uPnL", round(p["upnl"], 3), "SL", round(p["stop"], 2), "liq", round(p["liq"], 2))
        if a.force_entry and not forced and time.time() > end - a.minutes * 60 + 60:
            forced = True; sym = cfg["enabled_symbols"][0]
            try: print("manual paper long:", (await eng.handle("open", {"symbol": sym, "side": 1, "risk_pct": 0.25}))["position"]["id"])
            except Exception as e: print("manual open failed:", e)
    if a.force_entry:
        for s in list(eng.pf.positions):
            r = await eng.manual_close(s, 100); print("closed", s, r)
    for t in tasks: t.cancel()
    await eng.feed.close(); eng.save()
    print("DONE. telegram messages that would have been sent:", len(msgs))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--minutes", type=float, default=5); ap.add_argument("--data", default="/tmp/futdry")
    ap.add_argument("--force-entry", action="store_true"); asyncio.run(main(ap.parse_args()))

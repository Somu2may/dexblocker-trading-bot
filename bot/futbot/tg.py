"""Telegram front-end (python-telegram-bot). Only TELEGRAM_CHAT_ID may use it. PAPER futures only."""
import asyncio, logging, os, secrets, time, datetime as dt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from . import config
from .engine import ManualError, fp, side_txt
from .portfolio import IST

log = logging.getLogger("tg")
HELP = ("🤖 DexBlocker · PAPER (no real money, no wallet)\n"
        "Default: gmgn_meme — Solana memecoins. Futures stay available: /set strategy.name hf_maker (also donchian_15m, donchian_1h, scalper).\n"
        "Memecoin gates reject when data is missing. A price quote is not proof a token can be sold.\n"
        "Futures: BULK perps BTC · ETH · SOL, max 3x. hf trades are not pushed one by one.\n\n"
        "/report – live paper results vs backtest expectations (also sent automatically, see /set report.every_days N · report.at_ist HH:MM)\n"
        "/status – equity, breakers, data feed\n/positions – open positions\n/pnl – today + all time\n/signals – live signal per coin\n"
        "/long BTC [risk%] · /short ETH [risk%] – manual paper trade (confirm button)\n/close BTC [pct] · /close all\n"
        "/pause · /resume – auto entries off/on\n/settings · /set key value")

def midnight_ist(): return dt.datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

def build(engine, cfg):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip().strip("\"'"); chat = int(os.environ["TELEGRAM_CHAT_ID"])
    app = Application.builder().token(token).build()
    def guard(fn):
        async def w(u, c):
            if not u.effective_chat or u.effective_chat.id != chat: return
            try: await fn(u, c)
            except Exception as e: log.exception("cmd"); await u.message.reply_text(f"❌ error: {str(e)[:150]}")
        return w
    def next_report_txt():
        nxt = engine.next_report_at(); r = cfg.get("report") or {}
        return f"every {r.get('every_days')} days at {r.get('at_ist')} IST · next {dt.datetime.fromtimestamp(nxt, IST):%a %d %b %H:%M} IST" if nxt else "off (/set report.every_days 2)"
    def status_text():
        sn = engine.snapshot(); b = engine.meme.book.breakers if engine.is_meme() else engine.pf.breakers; st = engine.store.pnl_summary(midnight_ist())
        si = engine.strategy_info()
        meme = engine.is_meme()
        g = (cfg.get("strategy") or {}).get("gmgn") or {}
        mo = g.get("max_open_positions") if meme else cfg["risk"]["max_open_positions"]
        risk_line = (f"Size {g.get('position_size_pct')}% · stop -{g.get('stop_loss_pct')}% · daily cap {g.get('daily_loss_cap_pct')}%"
                     if meme else f"Risk {cfg['risk']['risk_pct']}%/trade · {cfg['risk']['leverage']:g}x max · daily cap {cfg['risk']['daily_loss_cap_pct']}%")
        marks = "" if meme else (" · mark " + " ".join(f"{s} {fp(v['mark'] or v['last'])}" for s, v in sn["prices"].items()))
        lines = [f"📊 PAPER {'memecoins' if meme else 'futures'} · {'▶️ RUNNING' if engine.running else '⏸ PAUSED'}",
                 f"Strategy: {'🧪 ' if si['experimental'] else ''}{si['label']}",
                 f"Equity ${sn['equity']:,.2f} (balance ${sn['balance']:,.2f}, start ${float(cfg['paper']['start_balance']):,.0f})",
                 f"Today: {st['pnl']:+.2f} USD · {st['wins']}W/{st['losses']}L",
                 f"Open: {len(sn['positions'])}/{mo} · margin used ${sn['used_margin']:,.0f}",
                 risk_line,
                 f"Breaker: {b.tripped or b.stale or 'none'} · loss streak {b.consec}",
                 f"Data: {sn['source'] or 'NONE'} ({'websocket' if sn['ws'] else 'REST'}){' · PROXY: ' + str(sn.get('proxy')) if sn.get('proxy') else ''}{marks}",
                 *([f"Skipped (BULK status): " + ", ".join(f"{k}-USD {v}" for k, v in sn['suspended'].items())] if sn.get("suspended") and not meme else []),
                 ("Universe: Solana memecoins" if meme else f"Coins: {', '.join(cfg['enabled_symbols'])}"),
                 f"Auto report: {next_report_txt()}"]
        return "\n".join(lines)
    def pos_text(): return engine.positions_text()
    async def start(u, c): await u.message.reply_text(HELP)
    async def status(u, c): await u.message.reply_text(status_text())
    async def positions(u, c): await u.message.reply_text(pos_text())
    async def pnl(u, c):
        d = engine.store.pnl_summary(midnight_ist()); a = engine.store.pnl_summary(0)
        await u.message.reply_text(f"💰 PAPER P&L (USD)\nToday: {d['pnl']:+.2f} · {d['wins']}W/{d['losses']}L · fees {d['fees']:.2f}\n"
                                   f"All time: {a['pnl']:+.2f} · {a['n']} trades · win rate {a['win_rate']:.0f}% · fees {a['fees']:.2f}\n"
                                   f"Equity now ${engine.equity():,.2f}")
    async def signals(u, c):
        if engine.is_meme():
            rows = engine._meme().scanner
            if not rows: return await u.message.reply_text("Scanner has not finished a pass yet.")
            lines = [f"{r.get('symbol')}: {r.get('decision')} — " + "; ".join((r.get("reasons") or [])[:2]) for r in rows[:12]]
            return await u.message.reply_text("📡 Memecoin scanner (rejected tokens include the reason)\n" + "\n".join(lines))
        if not engine.signals: return await u.message.reply_text("No signal check yet (runs at start and on every closed bar).")
        await u.message.reply_text(f"📡 Signals · {engine.strategy_info()['label']} (last closed bar)\n" + "\n".join(
            f"{s}: {d.get('reason')}" + (f" → {d['action']}" if d.get("side") else "") for s, d in engine.signals.items()))
    async def report(u, c): await u.message.reply_text(engine.report_text())
    async def pause(u, c): engine.pause(); await u.message.reply_text("⏸ Auto entries paused. Open positions keep their stops/targets. /resume to restart.")
    async def resume(u, c):
        left = engine.resume()
        await u.message.reply_text("▶️ Auto entries ON." + (f"\n⚠️ Still blocked: {left}" if left else ""))
    async def settings(u, c):
        keys = [k for k in config.flatten(cfg) if k.split(".")[0] in ("strategy", "exits", "risk", "enabled_symbols")]
        await u.message.reply_text("\n".join(f"{k} = {config.flatten(cfg)[k]}" for k in keys) + "\n\nChange: /set risk.risk_pct 0.5")
    async def set_(u, c):
        if len(c.args) < 2: return await u.message.reply_text("Usage: /set <key> <value>")
        try:
            if c.args[0] == "enabled_symbols":
                v = [x.strip().upper() for x in " ".join(c.args[1:]).replace(",", " ").split() if x.strip().upper() in cfg["symbols"]]
                cfg["enabled_symbols"] = v
            else: v = config.set_key(cfg, c.args[0], " ".join(c.args[1:]))
            config.save(cfg); await u.message.reply_text(f"✅ {c.args[0]} = {v}")
        except Exception as e: await u.message.reply_text(f"❌ {e}")
    pending = {}
    def park(a):
        for k in [k for k, v in pending.items() if v["exp"] < time.time()]: pending.pop(k, None)
        n = secrets.token_hex(6); a["exp"] = time.time() + 90; pending[n] = a; return n
    def kb(n, txt): return InlineKeyboardMarkup([[InlineKeyboardButton(txt, callback_data=f"fc:{n}"), InlineKeyboardButton("❌ Cancel", callback_data=f"fx:{n}")]])
    def opener(side):
        async def f(u, c):
            a = list(c.args or [])
            if not a: return await u.message.reply_text(f"Usage: /{'long' if side > 0 else 'short'} BTC [risk% 0.1-2]. PAPER only.")
            try: pv = await engine.manual_preview(a[0], side, float(a[1].rstrip("%")) if len(a) > 1 else None)
            except ManualError as e: return await u.message.reply_text(f"❌ {e.msg}")
            except ValueError: return await u.message.reply_text("❌ risk must be a number like 0.5")
            txt = (f"[MANUAL] PAPER {side_txt(side)} {pv['symbol']}-USD · {pv['lev']:g}x\nPrice {fp(pv['price'])} · SL {fp(pv['stop'])} · TP1 {fp(pv['tp1'])}\n"
                   f"Size {pv['qty']:.4g} = ${pv['notional']:,.0f} · margin ${pv['margin']:,.0f} · liq ≈ {fp(pv['liq'])}\n"
                   f"Risk ${pv['risk_usd']:.2f} ({pv['risk_pct']}%)" + (" · capped by leverage" if pv["capped_by_leverage"] else "") +
                   (f"\nBot signal now: {pv['signal']}" if pv.get("signal") else ""))
            if not pv["allowed"]: return await u.message.reply_text(txt + f"\n\n⛔ Not allowed: {pv['why']}")
            n = park({"kind": "open", "symbol": pv["symbol"], "side": side, "risk_pct": pv["risk_pct"]})
            await u.message.reply_text(txt, reply_markup=kb(n, f"✅ Confirm {side_txt(side)}"))
        return f
    async def close(u, c):
        a = list(c.args or [])
        if not a: return await u.message.reply_text("Usage: /close BTC [pct] · /close all")
        if a[0].lower() == "all":
            if not engine.pf.positions: return await u.message.reply_text("No open positions.")
            n = park({"kind": "close_all"}); return await u.message.reply_text(f"Close ALL {len(engine.pf.positions)} paper position(s)?", reply_markup=kb(n, "✅ Close all"))
        sym = a[0].upper().replace("USDT", ""); p = engine.pf.positions.get(sym)
        if not p: return await u.message.reply_text(f"No open position on {sym}.")
        try: pct = float(a[1].rstrip("%")) if len(a) > 1 else 100.0
        except ValueError: return await u.message.reply_text("❌ pct must be a number")
        if not 1 <= pct <= 100: return await u.message.reply_text("❌ pct must be 1-100")
        mk = engine.marks().get(sym) or p.entry
        n = park({"kind": "close", "symbol": sym, "pct": pct})
        await u.message.reply_text(f"[MANUAL] Close {pct:g}% of {side_txt(p.side)} {sym}? uPnL now {p.upnl(mk):+.2f} USD", reply_markup=kb(n, f"✅ Close {pct:g}%"))
    async def on_cb(u, c):
        q = u.callback_query
        if not q or not q.message or q.message.chat.id != chat: return
        kind, _, n = (q.data or "").partition(":"); a = pending.pop(n, None)
        if kind == "fx": await q.answer("Cancelled"); return await q.edit_message_reply_markup(None)
        if not a or a["exp"] < time.time(): await q.answer("Expired or already done"); return await q.edit_message_reply_markup(None)
        await q.answer("Executing (paper)…"); await q.edit_message_reply_markup(None)
        try:
            r = await engine.handle(a["kind"], a)
            txt = "✅ Done (paper)." if a["kind"] != "close" else f"✅ Closed {a['pct']:g}% {a['symbol']}-USD @ {fp(r['price'])}, P&L {r['pnl']:+.2f} USD"
        except ManualError as e: txt = f"❌ {e.msg}"
        except Exception as e: log.exception("cb"); txt = f"❌ error: {str(e)[:100]}"
        await q.message.reply_text(txt)
    for name, fn in [("start", start), ("help", start), ("status", status), ("positions", positions), ("pnl", pnl), ("signals", signals), ("report", report),
                     ("pause", pause), ("resume", resume), ("settings", settings), ("set", set_), ("long", opener(1)), ("short", opener(-1)), ("close", close)]:
        app.add_handler(CommandHandler(name, guard(fn)))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^f[cx]:"))
    tasks = []
    def notify(msg):
        try: loop = asyncio.get_running_loop()
        except RuntimeError: return
        async def _send():
            try: await app.bot.send_message(chat, msg)
            except Exception as e: log.warning("notify failed: %s", str(e)[:80])
        loop.create_task(_send())
    async def post_init(a):
        engine.notify = notify
        tasks[:] = await engine.start()
        si = engine.strategy_info()
        if engine.is_meme():
            src = engine.meme.data.status
            head = f"🤖 Memecoin bot online · PAPER · sources: {src}\nStrategy: {si['label']}\nSolana memecoins · size {cfg['strategy']['gmgn']['position_size_pct']}% · stop -{cfg['strategy']['gmgn']['stop_loss_pct']}%\n"
        else:
            head = (f"🤖 Futures bot online · PAPER · data: {engine.feed.active or 'NONE'}\n"
                    f"Strategy: {'🧪 EXPERIMENTAL ' if si['experimental'] else ''}{si['label']}\n"
                    f"{', '.join(cfg['enabled_symbols'])} · max {cfg['risk']['leverage']:g}x · risk {cfg['risk']['risk_pct']}%/trade\n")
        await a.bot.send_message(chat, head + f"Auto report: {next_report_txt()}\n"
                                       "Commands: /status /positions /pnl /signals /report /long /short /close /pause /resume")
        from telegram import BotCommand
        try: await a.bot.set_my_commands([BotCommand(n, d) for n, d in [("status", "equity, breakers, feed"), ("positions", "open positions"), ("pnl", "P&L"),
                ("signals", "live signals"), ("report", "live vs backtest"), ("long", "manual paper long"), ("short", "manual paper short"), ("close", "close position"),
                ("pause", "pause auto entries"), ("resume", "resume auto entries"), ("settings", "show settings"), ("help", "help")]])
        except Exception as e: log.warning("set_my_commands: %s", e)
    async def post_shutdown(a):
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        engine.save()
    app.post_init = post_init; app.post_shutdown = post_shutdown
    return app

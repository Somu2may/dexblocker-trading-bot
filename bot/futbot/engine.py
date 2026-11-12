"""Live PAPER engine for BULK perps: BULK market data -> strategy on each closed entry bar -> paper portfolio. No real orders.
Exits (stop / TP1 / trail) trigger on BULK MARK price, like BULK's own conditional orders."""
import asyncio, collections, logging, os, time, datetime as dt
from . import execution, strategy, indicators as ind
from .feeds import Feed
from .portfolio import Portfolio, Position, liq_price, ist_day, IST

log = logging.getLogger("engine")

def fp(x):
    if x is None: return "-"
    return f"{x:,.2f}" if x >= 100 else f"{x:,.3f}" if x >= 1 else f"{x:.5f}"

def side_txt(s): return "LONG" if s > 0 else "SHORT"

class ManualError(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.msg, self.code = msg, code

class Engine:
    def __init__(self, cfg, store, feed=None, notify=None, data_dir=None):
        self.cfg, self.store = cfg, store
        self.feed = feed or Feed(cfg)
        self.ref = Feed(cfg, client=self.feed.client, sources=cfg["data"].get("volume_ref_sources") or [], name="ref") if cfg["data"].get("volume_ref_sources") else None
        self.rules, self.suspended = {}, {}; self.notify = notify or (lambda m: None); self.data_dir = data_dir
        start = float(cfg["paper"]["start_balance"])
        self.pf = Portfolio(cfg, executor=execution.make(cfg), balance=store.get("balance", start), on_event=self._event)
        self.pf.seq = store.get("seq", 0)
        for d in (store.get("positions") or {}).values(): p = Position.from_dict(d); self.pf.positions[p.symbol] = p
        self.pf.breakers.load(store.get("breakers") or {})
        self.running = store.get("running", bool(cfg.get("autostart_trading", True)))
        self.signals, self.cooldown = {}, store.get("cooldown", {}) or {}
        self.funding_done = store.get("funding_done", {}) or {}
        self.lock = asyncio.Lock(); self.stats = collections.Counter(); self.started = time.time()
        self._stale_alert = False; self._last_eq_save = 0; self._last_breaker = self.pf.breakers.tripped
        self.last_signal_run = None
        self.hfeed = None; self.hist_src = {}; self.quotes = {}
        self.apply_overrides()
        since = store.get("strategy_since") or {}
        if self.strat() not in since: since[self.strat()] = time.time(); store.put("strategy_since", since)

    # ---------------- persistence + alerts ----------------
    def save(self):
        self.store.put("balance", self.pf.balance); self.store.put("seq", self.pf.seq)
        self.store.put("positions", {s: p.to_dict() for s, p in self.pf.positions.items()})
        self.store.put("breakers", self.pf.breakers.to_dict()); self.store.put("running", self.running)
        self.store.put("cooldown", self.cooldown); self.store.put("funding_done", self.funding_done)
        if getattr(self, "meme", None) is not None:
            self.meme.book.persist()

    def _event(self, e):
        t = e["type"]
        try:
            if t == "open":
                p = e["pos"]; self.store.fill(e["ts"], p["id"], p["symbol"], "open", p["side"], p["qty"], p["entry"], e["fee"], -e["fee"], p["reason"])
                self.stats["opened"] += 1
                if self._push(p): self.notify(self.fmt_open(p))
            elif t in ("partial", "close"):
                p = e["pos"]; self.store.fill(e["ts"], p["id"], e["symbol"], t, -p["side"], e["qty"], e["price"], e["fee"], e["pnl"], e["reason"])
                if t == "close":
                    tr = e["trade"]; self.store.trade(tr); self.stats["closed"] += 1
                    self.cooldown[e["symbol"]] = e["ts"] + 60 * float(self.cfg["strategy"].get("cooldown_min", 0))
                    if self._push(p): self.notify(self.fmt_close(tr))
                else:
                    self.notify(f"🎯 {'[MANUAL] ' if p.get('manual') else ''}{e['reason']} · {side_txt(p['side'])} {e['symbol']}-USD: closed {e['qty']:.4g} @ {fp(e['price'])}, "
                                f"P&L {e['pnl']:+.2f} USD. Stop → {fp(p['stop'])}" + (" (breakeven)" if 'TP1' in e['reason'] else ""))
            elif t == "funding":
                self.store.fill(e["ts"], "", e["symbol"], "funding", 0, 0, 0, 0, e["amount"], f"funding {e['rate'] * 100:.4f}%")
            self.save()
        except Exception as ex: log.exception("event persist failed: %s", ex)

    def _push(self, p):
        """Per-trade Telegram messages: always for manual trades; hf_maker trades only if strategy.hf.notify_trades (50+/day)."""
        return p.get("manual") or p.get("mode") != "hf" or bool(self.hcfg().get("notify_trades", False))

    def fmt_open(self, p):
        if p.get("mode") == "meme":
            return (f"🟢 PAPER BUY {p.get('symbol')} · gmgn_meme\n"
                    f"Mint {p.get('mint')}\n"
                    f"Fill ${p['entry']:.6g} (ref ${p.get('ref', p['entry']):.6g}, slip {p.get('slippage_pct', 0):.2f}%, fee ${p.get('fees', 0):.2f})\n"
                    f"Stop ${p['stop']:.6g} (-{self.cfg['strategy'].get('gmgn', {}).get('stop_loss_pct', 10)}%) · size ${p.get('notional0', 0):.2f}\n"
                    f"{p.get('reason')}\nPaper only. The fill model is not an on-chain sell.")
        risk = abs(p["entry"] - p["stop"]) * p["qty"]; eq = self.equity()
        if p.get("mode") == "trend":
            return (f"{'🟢' if p['side'] > 0 else '🔴'} {'[MANUAL] ' if p['manual'] else ''}{side_txt(p['side'])} {p['symbol']}-USD · {p['lev']:g}x · PAPER · 🧪 EXPERIMENTAL Donchian-{self.dcfg().get('tf', '1h')}\n"
                    f"Entry {fp(p['entry'])} | SL {fp(p['stop'])} ({(p['stop'] / p['entry'] - 1) * 100:+.2f}%, {self.dcfg().get('stop_atr', 3)} ATR)\n"
                    f"No take-profit: stop trails {self.dcfg().get('trail_atr', 3)} ATR below the highest high (moves on each {self.dcfg().get('tf', '1h')} close)\n"
                    f"Size {p['qty']:.4g} {p['symbol']} = ${p['notional0']:,.0f} notional, margin ${p['margin']:,.0f}\n"
                    f"Liq ≈ {fp(p['liq'])} | Risk ${risk:.2f} ({risk / eq * 100 if eq else 0:.2f}% of equity)\n"
                    f"{p['reason']}")
        return (f"{'🟢' if p['side'] > 0 else '🔴'} {'[MANUAL] ' if p['manual'] else ''}{side_txt(p['side'])} {p['symbol']}-USD · {p['lev']:g}x · PAPER\n"
                f"Entry {fp(p['entry'])} | SL {fp(p['stop'])} ({(p['stop'] / p['entry'] - 1) * 100:+.2f}%)\n"
                f"TP1 {fp(p['tp1'])} ({self.cfg['exits']['tp1_r']}R, {int(float(self.cfg['exits']['tp1_frac']) * 100)}%) then trail\n"
                f"Size {p['qty']:.4g} {p['symbol']} = ${p['notional0']:,.0f} notional, margin ${p['margin']:,.0f}\n"
                f"Liq ≈ {fp(p['liq'])} | Risk ${risk:.2f} ({risk / eq * 100 if eq else 0:.2f}% of equity)\n"
                f"{p['reason']}")

    def fmt_close(self, t):
        held = (t["closed"] - t["opened"]) / 60
        if t.get("setup") == "gmgn_meme":
            return (f"{'✅' if t['pnl'] > 0 else '❌'} PAPER CLOSE {t['symbol']} ({t['reason']})\n"
                    f"Entry ${t['entry']:.6g} → exit ${t['exit']:.6g} · held {held:.0f}m\n"
                    f"P&L {t['pnl']:+.2f} USD ({t['r_mult']:+.2f}R) · fees {t['fees']:.2f}\n"
                    f"Equity ${self.equity():,.2f}")
        return (f"{'✅' if t['pnl'] > 0 else '❌'} {'[MANUAL] ' if t['manual'] else ''}CLOSED {side_txt(t['side'])} {t['symbol']}-USD ({t['reason']})\n"
                f"Entry {fp(t['entry'])} → Exit {fp(t['exit'])} · held {f'{held / 60:.1f}h' if held >= 120 else f'{held:.0f}m'}\n"
                f"P&L {t['pnl']:+.2f} USD ({t['r_mult']:+.2f}R) · fees {t['fees']:.2f}, funding {t['funding']:+.2f}\n"
                f"Equity ${self.equity():,.2f}")

    # ---------------- strategy selection ----------------
    def strat(self): return self.cfg["strategy"].get("name") or "gmgn_meme"
    def is_dc(self): return strategy.is_donchian(self.strat())
    def is_hf(self): return self.strat() == "hf_maker"
    def is_meme(self): return self.strat() == "gmgn_meme"
    def _meme(self):
        if getattr(self, "meme", None) is None:
            from .meme_run import Runner
            self.meme = Runner(self)
        return self.meme
    def hcfg(self): return self.cfg["strategy"].get("hf") or {}
    def apply_overrides(self):
        """hf_maker: its own risk %, max open positions and loss-streak pause; other strategies use `risk:` as is."""
        self.pf.ov = (self.hcfg().get("risk") or {}) if self.is_hf() else {}
        self.pf.hf_thr = float(self.hcfg().get("fill_through_pct", 0.01)) / 100
    def dcfg(self): return strategy.donchian_cfg(self.cfg["strategy"], self.strat() if self.is_dc() else "donchian_15m")
    def entry_sec(self):
        if self.is_hf(): return 60
        return strategy.TF_SEC[self.dcfg().get("tf", "1h")] if self.is_dc() else strategy.TF_SEC[self.cfg["strategy"]["entry_tf"]]
    def setup_tag(self): return strategy.SETUP.get(self.strat(), "scalp")
    def strategy_info(self):
        n = self.strat(); d = self.dcfg(); dc = self.is_dc(); hf = self.is_hf(); h = self.hcfg()
        since = self.store.get("strategy_since") or {}
        if n not in since: since[n] = time.time(); self.store.put("strategy_since", since)     # switched at runtime via /set
        if n == "gmgn_meme":
            g = self.cfg["strategy"].get("gmgn") or {}
            from .gmgn_http import GmgnClient
            keyed = GmgnClient().enabled
            if keyed:
                summary = (
                    f"Solana memecoins, paper only, gmgn mode. {g.get('min_smart_buyers', 2)}+ smart-money buyers, "
                    f"1h momentum {g.get('min_momentum_pct', 3)}–{g.get('max_chase_pct', 100)}%, "
                    f"size {g.get('position_size_pct', 1.5)}%, stop -{g.get('stop_loss_pct', 10)}%. "
                    "Missing security data rejects. A price quote is not a sell proof.")
            else:
                summary = (
                    "Solana memecoins, paper only, no-key mode (GMGN_API_KEY missing). DexScreener + RugCheck. "
                    f"h1 buy/sell >= {g.get('nokey_min_buy_sell_ratio', 1.1)}, 5m buys >= sells, "
                    f"1h momentum {g.get('min_momentum_pct', 3)}–{g.get('max_chase_pct', 100)}%, "
                    f"size {g.get('position_size_pct', 1.5)}%, stop -{g.get('stop_loss_pct', 10)}%. "
                    "Dev history, bundlers, snipers, and smart-money are skipped. "
                    "Missing data on a remaining gate rejects.")
            return {"name": n, "label": strategy.STRATEGIES[n], "experimental": False, "paper": True, "tf": "scan", "trend_tf": None,
                    "channel": None, "long_only": True, "since": since.get(n), "risk_pct": g.get("position_size_pct"),
                    "max_open": g.get("max_open_positions"), "daily_loss_cap_pct": g.get("daily_loss_cap_pct"),
                    "stop_loss_pct": g.get("stop_loss_pct"), "summary": summary,
                    "honesty": "futbot/meme_rules.py HONESTY",
                    "data_mode": "gmgn" if keyed else "nokey"}
        if hf:
            e = h.get("expect") or {}; rk = h.get("risk") or {}
            return {"name": n, "label": strategy.STRATEGIES[n], "experimental": True, "paper": True, "tf": "1m", "trend_tf": None, "channel": None,
                    "long_only": False, "since": since.get(n), "target_trades_per_day": h.get("target_trades_per_day", 50),
                    "risk_pct": rk.get("risk_pct"), "max_open": rk.get("max_open_positions"), "expect": e,
                    "summary": (f"maker BUY/SELL quotes at EMA{h.get('ema', 60)} ∓ {h.get('band_atr', 4)} ATR(1m) every minute, fill only on trade-through; "
                                f"exit maker at EMA, stop {h.get('stop_atr', 8)} ATR (taker), max {h.get('max_hold_min', 120)} min · risk {rk.get('risk_pct')}%/trade")}
        return {"name": n, "label": strategy.STRATEGIES.get(n, n), "experimental": dc, "paper": True,
                "tf": d.get("tf", "1h") if dc else self.cfg["strategy"]["entry_tf"],
                "trend_tf": d.get("trend_tf", "4h") if dc else self.cfg["strategy"]["trend_tf"],
                "channel": d.get("channel", 20) if dc else None, "long_only": bool(d.get("long_only", True)) if dc else False,
                "since": since.get(n), "summary": strategy.summary(n, d)}

    # ---------------- state ----------------
    def px(self, sym):
        """Execution reference price: BULK mark (tracks book/oracle); the last-trade price can sit unchanged for minutes on thin BULK books."""
        return self.feed.mark.get(sym) or self.feed.price.get(sym)
    def marks(self): return {s: self.feed.mark.get(s) or self.feed.price.get(s) for s in self.cfg["symbols"] if self.feed.price.get(s) or self.feed.mark.get(s)}
    def equity(self):
        if self.is_meme():
            return self._meme().book.equity()
        return self.pf.equity(self.marks())
    def stale_syms(self):
        lim = float(self.cfg["data"]["stale_after_sec"])
        return [s for s in self.cfg["enabled_symbols"] if s not in self.suspended and self.feed.age(s) > lim]
    def tradable(self): return [s for s in self.cfg["enabled_symbols"] if s not in self.suspended]

    async def load_rules(self):
        """BULK exchangeInfo: tickSize / sizeIncrement / minNotional / maxLeverage; symbols with status != TRADING are skipped."""
        src = self.feed.srcs.get("bulk")
        if not src: return
        try: info = await src.exchange_info()
        except Exception as e: log.warning("exchangeInfo: %s", e); return
        for s in self.cfg["symbols"]:
            x = info.get(s)
            st = (x or {}).get("status", "MISSING")
            if x: self.rules[s] = x; self.pf.rules[s] = x
            was = s in self.suspended
            if st != "TRADING":
                self.suspended[s] = st
                if not was: log.warning("%s-USD status %s -> skipped", s, st); self.notify(f"⚠️ BULK {s}-USD is {st} - skipped (no new entries).")
            elif was:
                self.suspended.pop(s); self.notify(f"✅ BULK {s}-USD is TRADING again.")

    async def refresh_books(self, syms):
        src = self.feed.srcs.get("bulk")
        if not src or self.feed.active != "bulk": return
        for s in syms:
            try: b, a = await src.book(s, 20); self.pf.ex.books[s] = (b, a, time.time())
            except Exception as e: log.debug("book %s: %s", s, e)

    def snapshot(self):
        if self.is_meme():
            return self._meme().snapshot()
        m = self.marks(); b = self.pf.breakers; eq = self.pf.equity(m)
        pos = []
        for s, p in self.pf.positions.items():
            mk = m.get(s) or p.mark or p.entry
            pos.append({**p.to_dict(), "mark": mk, "last": self.feed.price.get(s), "upnl": p.upnl(mk),
                        "upnl_pct_margin": p.upnl(mk) / (p.margin * p.qty / p.qty0) * 100 if p.margin else 0})
        return {"heartbeat": time.time(), "running": self.running, "breaker": b.tripped, "stale": b.stale, "consec_losses": b.consec,
                "day_start_eq": b.day_start_eq, "balance": self.pf.balance, "equity": eq, "used_margin": self.pf.used_margin(),
                "source": self.feed.active, "sources": self.feed.status, "proxy": self.feed.proxy, "venue": "BULK",
                "ref_source": self.ref.active if self.ref else None, "suspended": self.suspended,
                "rules": {s: {k: r.get(k) for k in ("status", "tickSize", "sizeIncrement", "minNotional", "maxLeverage")} for s, r in self.rules.items()}, "ws": self.feed.ws_ok, "switches": self.feed.switches,
                "prices": {s: {"last": self.feed.price.get(s), "mark": self.feed.mark.get(s), "oracle": self.feed.oracle.get(s), "age": round(self.feed.age(s), 1)} for s in self.cfg["symbols"]},
                "funding": self.feed.funding, "positions": pos, "signals": self.signals, "stats": dict(self.stats),
                "enabled_symbols": self.cfg["enabled_symbols"], "last_signal_run": self.last_signal_run,
                "strategy": self.strategy_info(), "history_source": self.hist_src,
                "quotes": {k: {"bid": next((x[1] for x in q["q"] if x[0] > 0), None), "ask": next((x[1] for x in q["q"] if x[0] < 0), None), "until": q["until"]}
                           for k, q in self.quotes.items()}, "pauses": self.pf.breakers.pauses, "pause": self.pf.breakers.paused()}

    # ---------------- loops ----------------
    async def start(self):
        self._loops = []
        self._mode = None
        await self._enter_mode(self.is_meme())
        return [asyncio.create_task(self._mode_supervisor()), asyncio.create_task(self.report_loop())]

    async def _enter_mode(self, meme):
        for t in list(getattr(self, "_loops", []) or []):
            t.cancel()
        if getattr(self, "_loops", None):
            await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops = []
        if meme:
            self._meme()
            self._loops = [asyncio.create_task(self.meme.loop()), asyncio.create_task(self.meme.price_loop())]
        else:
            await self.feed.probe(); self.feed.restart_ws()
            if self.ref: await self.ref.probe()
            await self.load_rules(); await self.feed.refresh_funding()
            self._loops = [asyncio.create_task(x) for x in (self.feed.rest_poll(), self.price_loop(), self.signal_loop(), self.funding_loop(), self.slow_loop())]
        self._mode = bool(meme)
        log.info("strategy mode %s", "gmgn_meme" if meme else self.strat())

    async def _mode_supervisor(self):
        try:
            while True:
                try:
                    if bool(self.is_meme()) != bool(self._mode):
                        await self._enter_mode(self.is_meme())
                        self.notify(f"Strategy loop switched to {self.strat()} · PAPER")
                except Exception as e:
                    log.warning("mode switch: %s", str(e)[:150])
                await asyncio.sleep(3)
        finally:
            for t in list(getattr(self, "_loops", []) or []):
                t.cancel()
            if getattr(self, "_loops", None):
                await asyncio.gather(*self._loops, return_exceptions=True)

    async def slow_loop(self):
        """exchangeInfo refresh, L2 book cache for open positions, fail back to BULK after a proxy period."""
        n = 0
        while True:
            try:
                n += 1
                if n % max(1, int(float(self.cfg["data"]["exchange_info_refresh_sec"]) / 5)) == 0: await self.load_rules()
                if self.pf.positions: await self.refresh_books(list(self.pf.positions))
                if self.feed.active != self.feed.order[0] and n % 12 == 0:
                    old = self.feed.active; self.feed.active = None; await self.feed.probe()
                    if self.feed.active != old: self.feed.restart_ws(); self.notify(f"🔁 Market data source: {self.feed.active}")
            except Exception as e: log.warning("slow loop: %s", e)
            await asyncio.sleep(float(self.cfg["data"].get("book_refresh_sec", 5)))

    async def price_loop(self):
        while True:
            try:
                now = time.time()
                async with self.lock:
                    for s in list(self.pf.positions):
                        px = self.feed.mark.get(s) or self.feed.price.get(s)        # BULK triggers conditionals on mark price
                        if px and self.feed.age(s) < float(self.cfg["data"]["stale_after_sec"]):
                            self.pf.on_price(s, px, now)
                    self.check_quotes(now)
                    eq = self.equity(); b = self.pf.breakers
                    b.roll(now, eq); b.check_daily(eq)
                    st = self.stale_syms()
                    b.stale = f"stale data: no price for {', '.join(st)} > {self.cfg['data']['stale_after_sec']}s" if st else None
                    if st and not self._stale_alert and now - self.started > 60:
                        self._stale_alert = True; self.notify(f"⚠️ {b.stale}. New entries paused" + (", open positions can't be managed until data is back!" if self.pf.positions else "."))
                    elif not st and self._stale_alert:
                        self._stale_alert = False; self.notify(f"✅ Market data back ({self.feed.active}). Entries allowed again.")
                    if b.tripped != self._last_breaker and b.tripped and b.tripped.startswith("daily"):
                        days = self.store.get("daily_cap_days") or []
                        if ist_day(now) not in days: self.store.put("daily_cap_days", (days + [ist_day(now)])[-400:])
                    if b.tripped != self._last_breaker:
                        if b.tripped: self.notify(f"🛑 Circuit breaker: {b.tripped}. No new entries" + (" until tomorrow (IST) or /resume." if "daily" not in b.tripped else " until tomorrow (IST)."))
                        self._last_breaker = b.tripped; self.save()
                    if now - self._last_eq_save > 300:
                        self._last_eq_save = now; self.store.equity(now, eq, self.pf.balance); self.save()
                if self.data_dir:
                    with open(os.path.join(self.data_dir, "heartbeat"), "w") as f: f.write(str(now))
            except Exception as e: log.exception("price loop: %s", e)
            await asyncio.sleep(1)

    async def signal_loop(self):
        if self.is_dc() or self.is_hf():       # warm-up check right away: history loaded, signals shown, trails updated; entries wait for the next entry-tf close
            for s in self.tradable():
                try: await self.evaluate(s, warmup=True)
                except Exception as e: log.warning("warm-up %s: %s", s, str(e)[:150])
        while True:
            etf = self.entry_sec()
            now = time.time(); nxt = (now // etf + 1) * etf
            await asyncio.sleep(nxt - now + 3)
            self.last_signal_run = time.time()
            for s in self.tradable():
                try: await self.evaluate(s)
                except Exception as e: log.warning("evaluate %s: %s", s, str(e)[:150]); self.stats["eval errors"] += 1

    async def bars(self, sym):
        s = self.cfg["strategy"]; etf, htf = s["entry_tf"], s["trend_tf"]
        n_e = 300 if etf != "1m" else 600
        eb = await self.feed.candles(sym, etf, n_e)
        want = (time.time() // strategy.TF_SEC[etf]) * strategy.TF_SEC[etf] - strategy.TF_SEC[etf]
        for _ in range(3):                           # newest closed bar may need a couple of seconds to be confirmed
            if eb and eb[-1][0] >= want: break
            await asyncio.sleep(2); eb = await self.feed.candles(sym, etf, n_e)
        hb = await self.feed.candles(sym, htf, 600)
        if self.ref and self.ref.active:      # BULK volume is tiny/often 0 -> reference venue volume for the volume filter
            try:
                rv = {b[0]: b[5] for b in await self.ref.candles(sym, etf, n_e)}
                eb = [b[:5] + (rv.get(b[0], 0.0),) for b in eb]
            except Exception as e: log.warning("ref volume %s: %s", sym, str(e)[:80]); eb = [b[:5] + (0.0,) for b in eb]
        return eb, hb

    async def history(self, sym, tf, n, trend=None, p=None):
        """Closed bars for the Donchian strategy: BULK klines first, reference venue (OKX/Hyperliquid/...) if BULK has too few."""
        sec = strategy.TF_SEC[tf]
        p = p if p is not None else self.dcfg()
        if trend is None: trend = tf == p.get("trend_tf", "4h")
        want = (time.time() // sec) * sec - sec
        bars, src = [], self.feed.active
        try:
            for _ in range(6):                   # the just-closed bar can take a few seconds to appear on BULK
                bars = strategy.closed_only(await self.feed.candles(sym, tf, n), sec, time.time())
                if bars and bars[-1][0] >= want: break
                await asyncio.sleep(2)
        except Exception as e: log.warning("history %s %s: %s", sym, tf, str(e)[:100])
        minimum = int(p["min_bars"]) if p.get("min_bars") else int(p.get("trend_ema", 50)) + 5 if trend else int(p.get("channel", 20)) + int(p.get("atr_len", 14)) + 30
        if len(bars) < minimum or bars[-1][0] < want:
            fb = p.get("fallback_history") or []
            if fb:
                if self.hfeed is None: self.hfeed = Feed(self.cfg, client=self.feed.client, sources=fb, name="history"); await self.hfeed.probe()
                try:
                    alt = strategy.closed_only(await self.hfeed.candles(sym, tf, n), sec, time.time())
                    if len(alt) > len(bars) or (alt and bars and alt[-1][0] > bars[-1][0]): bars, src = alt, f"{self.hfeed.active} (fallback)"
                except Exception as e: log.warning("fallback history %s %s: %s", sym, tf, str(e)[:100])
        self.hist_src[f"{sym} {tf}"] = f"{src} {len(bars)} bars"
        return bars

    async def evaluate(self, sym, warmup=False):
        self.apply_overrides()
        if self.is_meme():
            return
        if self.is_hf(): return await self.evaluate_hf(sym, warmup)
        if self.is_dc(): return await self.evaluate_donchian(sym, warmup)
        s = self.cfg["strategy"]; eb, hb = await self.bars(sym)
        d = strategy.evaluate_latest(eb, hb, s, s["entry_tf"], s["trend_tf"])
        d = {k: v for k, v in d.items()}; d["symbol"] = sym; d["checked"] = time.time(); d["action"] = "none"
        self.stats["evaluations"] += 1
        if d["side"]:
            self.stats["signals"] += 1
            ok, why = await self._entry_gate(sym, d)
            d["action"] = "opened" if ok else f"skipped: {why}"
            if ok:
                await self.refresh_books([sym])
                async with self.lock:
                    px = self.px(sym) or d["price"]
                    self.pf.open(sym, d["side"], px, d["stop_dist"], time.time(), atr=d["atr"], setup=d["setup"], reason=d["reason"], equity=self.equity())
            self.store.signal_log(sym, d["side"], d.get("setup"), d["reason"], d["action"])
        self.signals[sym] = d; self.store.signal(sym, d)
        log.info("signal %s %s -> %s", sym, d.get("reason"), d["action"])

    async def evaluate_donchian(self, sym, warmup=False):
        p = self.dcfg(); tag = self.setup_tag()
        b1 = await self.history(sym, p.get("tf", "1h"), strategy.hist_n(p, "entry"), trend=False)
        b4 = await self.history(sym, p.get("trend_tf", "4h"), strategy.hist_n(p, "trend"), trend=True)
        d = strategy.donchian_latest(b1, b4, p); d["setup"] = tag
        d["symbol"] = sym; d["checked"] = time.time(); d["action"] = "none"; d["strategy"] = self.strat()
        d["diag"]["history"] = self.hist_src.get(f"{sym} {p.get('tf', '1h')}")
        self.stats["evaluations"] += 1
        pos = self.pf.positions.get(sym)
        if pos and pos.mode == "trend" and b1 and d.get("atr"):        # chandelier trail on the closed entry-tf bar(s) since entry
            since = [b for b in b1 if b[0] >= pos.opened - 60]
            if since:
                async with self.lock:
                    moved = self.pf.trail_bar(sym, max(b[2] for b in since), min(b[3] for b in since), d["atr"], float(p.get("trail_atr", 3.0)), time.time())
                if moved: log.info("trail %s -> %s", sym, moved); self.save()
            d["diag"]["trail_stop"] = self.pf.positions[sym].stop if sym in self.pf.positions else None
        if d["side"]:
            self.stats["signals"] += 1
            if warmup: ok, why = False, f"warm-up check at start - entries only on a fresh {p.get('tf', '1h')} close"
            else: ok, why = await self._entry_gate(sym, d)
            d["action"] = "opened" if ok else f"skipped: {why}"
            if ok:
                await self.refresh_books([sym])
                async with self.lock:
                    px = self.px(sym) or d["price"]
                    self.pf.open(sym, d["side"], px, d["stop_dist"], time.time(), atr=d["atr"], setup=tag, reason=d["reason"],
                                 equity=self.equity(), mode="trend", trail_mult=float(p.get("trail_atr", 3.0)))
            self.store.signal_log(sym, d["side"], tag, d["reason"], d["action"])
        self.signals[sym] = d; self.store.signal(sym, d)
        log.info("signal %s %s -> %s", sym, d.get("reason"), d["action"])

    # ---------------- hf_maker: maker quotes on every closed 1m bar, filled by the 1 s price loop ----------------
    async def _quote_gate(self, sym):
        s = self.cfg["strategy"]; now = time.time()
        if not self.running: return False, "paused"
        if sym not in self.cfg["enabled_symbols"]: return False, "symbol off"
        if sym in self.suspended: return False, f"BULK status {self.suspended[sym]}"
        if self.feed.proxy: return False, f"data proxy: {self.feed.proxy}"
        ok, why = self.pf.can_open(sym, now)
        if not ok: return False, why
        if int(now // 3600 % 24) in set(s.get("skip_hours_utc") or []): return False, "skip hour"
        f = self.feed.funding.get(sym)
        if f and abs(f["rate"]) * 100 >= float(s["funding_skip_abs_pct"]) and 0 <= f["next"] - now <= 60 * float(s["funding_skip_min"]):
            return False, f"funding {f['rate'] * 100:.3f}% due in {int((f['next'] - now) / 60)}m"
        if not self.px(sym) or self.feed.age(sym) > float(self.cfg["data"]["stale_after_sec"]): return False, "no live price"
        return True, "ok"

    async def evaluate_hf(self, sym, warmup=False):
        p = self.hcfg()
        bars = await self.history(sym, "1m", strategy.hist_n(p, "entry"), trend=False, p=p)
        d = strategy.hf_quotes(bars, p)
        d["symbol"] = sym; d["checked"] = time.time(); d["action"] = "none"; d["strategy"] = "hf_maker"
        d["diag"]["history"] = self.hist_src.get(f"{sym} 1m"); self.stats["evaluations"] += 1
        async with self.lock: self.quotes.pop(sym, None)
        if sym in self.pf.positions: d["action"] = "in position (no new quotes)"
        elif d["quotes"]:
            ok, why = await self._quote_gate(sym)
            if ok and warmup: ok, why = False, "warm-up at start - quoting from the next 1m close"
            if ok:
                bar_close = (bars[-1][0] + 60) if bars else time.time()
                q = [(sd, self.pf.round_px(sym, lvl, up=sd < 0), sdist, tgt) for sd, lvl, sdist, tgt in d["quotes"]]
                async with self.lock: self.quotes[sym] = {"q": q, "until": max(bar_close, time.time() - 5) + 60, "atr": d["atr"], "reason": d["reason"]}
                d["action"] = "quotes resting (maker)"; self.stats["quotes"] += 1
            else: d["action"] = f"no quotes: {why}"
        d["quotes"] = [list(x) for x in d["quotes"]]
        self.signals[sym] = d; self.store.signal(sym, d)
        log.debug("hf %s %s -> %s", sym, d.get("reason"), d["action"])

    def check_quotes(self, now):
        """Called under self.lock every second. A resting maker quote fills at its limit price if the BULK mark trades THROUGH it
        by fill_through_pct (touching is not a fill). One fill per symbol; the other side is cancelled."""
        thr = self.pf.hf_thr; p = self.hcfg()
        for sym, Q in list(self.quotes.items()):
            if now > Q["until"] or sym in self.pf.positions: self.quotes.pop(sym, None); continue
            px = self.px(sym)
            if not px or self.feed.age(sym) > float(self.cfg["data"]["stale_after_sec"]): continue
            for sd, lvl, sdist, tgt in Q["q"]:
                if (sd > 0 and px < lvl * (1 - thr)) or (sd < 0 and px > lvl * (1 + thr)):
                    self.quotes.pop(sym, None)
                    ok, why = self.pf.can_open(sym, now)
                    if not ok: self.stats["fills blocked"] += 1; break
                    reason = f"maker {'BUY' if sd > 0 else 'SELL'} filled @ {fp(lvl)} (EMA{p.get('ema', 60)} {'-' if sd > 0 else '+'} {p.get('band_atr', 4)} ATR) · target {fp(tgt)}"
                    pos = self.pf.open(sym, sd, lvl, sdist, now, atr=Q.get("atr"), setup="hf_maker", reason=reason, equity=self.equity(),
                                       mode="hf", maker=True, target=tgt, hold_min=float(p.get("max_hold_min", 120)))
                    if pos:
                        self.stats["maker fills"] += 1
                        self.store.signal_log(sym, sd, "hf_maker", reason, "filled (maker)")
                    break

    def report(self):
        """Live paper results of the active strategy vs backtest expectations."""
        info = self.strategy_info(); since = info["since"] or self.started
        tag = "gmgn_meme" if self.is_meme() else self.setup_tag()
        if self.is_meme():
            T = [t for t in self.store.trades(since) if not t["manual"] and t.get("setup") == "gmgn_meme"]
        else:
            T = [t for t in self.store.trades(since) if not t["manual"] and (t["setup"] == tag if (self.is_dc() or self.is_hf()) else t["setup"] not in strategy.SETUP.values())]
        weeks = max((time.time() - since) / (7 * 86400), 1 / 7)
        w = [t for t in T if t["pnl"] > 0]; g = sum(t["pnl"] for t in w); ls = -sum(t["pnl"] for t in T if t["pnl"] < 0)
        eq, peak, dd = 0.0, 0.0, 0.0; start = float(self.cfg["paper"]["start_balance"])
        for t in T: eq += t["pnl"]; peak = max(peak, eq); dd = max(dd, (peak - eq) / (start + peak) * 100)
        live = {"trades": len(T), "trades_per_week": round(len(T) / weeks, 2), "win_pct": round(len(w) / len(T) * 100, 1) if T else None,
                "pf": round(g / ls, 2) if ls else (None if not T else 99.0), "avg_r": round(sum(t["r_mult"] or 0 for t in T) / len(T), 3) if T else None,
                "net": round(sum(t["pnl"] for t in T), 2), "net_pct": round(sum(t["pnl"] for t in T) / start * 100, 2), "fees": round(sum(t["fees"] or 0 for t in T), 2), "max_dd_pct": round(dd, 2),
                "open": len(self._meme().book.positions) if self.is_meme() else len([p for p in self.pf.positions.values() if p.mode in ("trend", "hf")]),
                "days": round((time.time() - since) / 86400, 1)}
        days = max((time.time() - since) / 86400, 1 / 24)
        live["trades_per_day"] = round(len(T) / days, 1)
        live["cap_days"] = len([d for d in (self.store.get("daily_cap_days") or []) if d >= ist_day(since)])
        live["streak_pauses"] = (self._meme().book.breakers.pauses if self.is_meme() else self.pf.breakers.pauses)
        exp = {} if self.is_meme() else ((self.dcfg().get("expect") or {}) if self.is_dc() else (self.hcfg().get("expect") or {}) if self.is_hf() else {})
        n = len(T); bpf = exp.get("pf")
        if self.is_meme() and n < 10:
            verdict = f"too early: {n} closed paper memecoin trades (no backtest for gmgn_meme; need ~20-30 before PF means much)"
        elif self.is_meme():
            verdict = "no backtest for gmgn_meme — judge the live paper profit factor and the risk metrics below"
        elif n < 10: verdict = f"too early: {n} closed trades (need ~20-30 before PF/win% mean anything; backtest PF {bpf if bpf is not None else 'n/a'})"
        elif live["pf"] is not None and live["pf"] >= 1.0:
            verdict = ("profitable so far (PF >= 1)" + (f", better than the backtest PF {bpf} - could be luck, small sample" if bpf is not None and bpf < 1 else " - in line with or above the backtest, still a small sample"))
        elif live["pf"] is not None and live["pf"] < 0.8 and n >= 20:
            verdict = "LOSING (PF < 0.8 after 20+ trades)" + (" - as the backtest predicted; consider stopping or switching to donchian_1h" if bpf is not None and bpf < 1 else " - underperforming the backtest; consider stopping the experiment")
        else: verdict = "below breakeven but within normal noise for this sample size"
        from . import metrics
        live["qs"] = metrics.report_metrics(T, start, since, time.time())
        return {"strategy": info, "live": live, "expect": exp, "verdict": verdict}

    def report_text(self):
        r = self.report(); L, E, info = r["live"], r["expect"], r["strategy"]
        f = lambda v, suf="": "—" if v is None else f"{v}{suf}"
        bt_days = f" ({E.get('days', 700)} d)" if E.get("net_pct") is not None else ""
        lines = [f"🧪 Strategy report · {info['label']} · PAPER", f"Live since {dt.datetime.fromtimestamp(info['since'] or self.started, IST):%d %b %Y %H:%M} IST ({L['days']} days)",
                 "", "metric        live      backtest",
                 f"trades        {L['trades']:<9} —", f"trades/week   {f(L['trades_per_week']):<9} {f(E.get('trades_per_week'))}",
                 f"win %         {f(L['win_pct']):<9} {f(E.get('win_pct'))}", f"profit factor {f(L['pf']):<9} {f(E.get('pf'))}",
                 f"avg R         {f(L['avg_r']):<9} {f(E.get('avg_r'))}", f"max DD %      {f(L['max_dd_pct']):<9} {f(E.get('max_dd_pct'))}",
                 f"net %         {f(L['net_pct']):<9} {f(E.get('net_pct'))}{bt_days}",
                 "", f"Net {L['net']:+.2f} USD · fees {L['fees']:.2f} · equity ${self.equity():,.2f} · open bot positions {L['open']}"]
        if self.is_meme():
            lines.append("Memecoin fills are a model (fee + slippage from size versus liquidity), not an on-chain sell.")
        if self.is_hf():
            tgt = info.get("target_trades_per_day", 50)
            lines[4:4] = [f"trades/day    {f(L['trades_per_day']):<9} {f(E.get('trades_per_day'))}  (target {tgt}{' ✅' if L['trades_per_day'] >= tgt else ' ⚠️ below'})"]
            lines.append(f"Daily 3% loss cap hit on {L['cap_days']} day(s) (no new trades for the rest of that IST day) · loss-streak pauses {L['streak_pauses']}")
            if E.get("bulk_60d"): b = E["bulk_60d"]; lines.append(f"Backtest on BULK's own last 60 d: {b['trades_per_day']} trades/day, PF {b['pf']}, net {b['net_pct']:+}%.")
        if E.get("ret_pct_per_90d_range"): lines.append(f"Backtest 90-day windows ranged {E['ret_pct_per_90d_range'][0]:+}% to {E['ret_pct_per_90d_range'][1]:+}%.")
        if L.get("qs"):
            from . import metrics
            lines += ["", metrics.metrics_text(L["qs"])]
        lines += ["", f"Verdict: {r['verdict']}"]
        return "\n".join(lines)

    # ---------------- automatic report (every N days at HH:MM IST, config `report:`) ----------------
    def _report_sched(self):
        r = self.cfg.get("report") or {}; every = int(r.get("every_days") or 0)
        hh, _, mm = str(r.get("at_ist") or "20:00").partition(":")
        try: anchor = dt.date.fromisoformat(str(r.get("anchor_date") or "2026-11-02"))
        except ValueError: anchor = dt.date(2026, 11, 2)
        return every, int(hh or 20), int(mm or 0), anchor

    def next_report_at(self, now=None):
        """Next scheduled auto report (epoch seconds) or None when report.every_days is 0."""
        every, hh, mm, anchor = self._report_sched()
        if every <= 0: return None
        now = now or time.time(); d = dt.datetime.fromtimestamp(now, IST).date(); last = self.store.get("auto_report_last")
        for k in range(0, every * 2 + 2):
            day = d + dt.timedelta(days=k)
            if (day - anchor).days % every: continue
            at = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).timestamp()
            if day.isoformat() == last: continue
            if at >= now: return at
            if k == 0: return now                                     # today's report is due right now (catch-up)
        return None

    def report_due(self, now=None):
        """IST date key if an auto report should go out now (scheduled day, time passed, not sent yet today), else None.
        A bot restarted later the same evening still sends that day's report; a missed day is not sent the next day."""
        every, hh, mm, anchor = self._report_sched()
        if every <= 0: return None
        now = now or time.time(); n = dt.datetime.fromtimestamp(now, IST); day = n.date()
        if (day - anchor).days % every or (n.hour, n.minute) < (hh, mm): return None
        key = day.isoformat()
        return None if self.store.get("auto_report_last") == key else key

    def positions_text(self):
        if self.is_meme():
            return self._meme().positions_text()
        sn = self.snapshot()
        if not sn["positions"]: return "No open positions."
        out = []
        for p in sn["positions"]:
            out.append(f"{'🟢' if p['side'] > 0 else '🔴'} {side_txt(p['side'])} {p['symbol']}-USD {p['lev']:g}x{' [MANUAL]' if p['manual'] else ''}\n"
                       f"Entry {fp(p['entry'])} · Mark {fp(p['mark'])} · uPnL {p['upnl']:+.2f} ({p['upnl_pct_margin']:+.1f}% on margin)\n"
                       + (f"SL/trail {fp(p['stop'])} (initial {fp(p['stop0'])}) · no TP · Liq {fp(p['liq'])}\n" if p.get("mode") == "trend" else
                          f"SL {fp(p['stop'])} · TP1 {fp(p['tp1'])}{' ✅' if p['tp1_done'] else ''} · Liq {fp(p['liq'])}\n") +
                       f"Size {p['qty']:.4g} ({p['qty'] / p['qty0'] * 100:.0f}% left) · held {(time.time() - p['opened']) / 3600:.1f}h")
        return "\n\n".join(out)

    def auto_report_text(self):
        every, hh, mm, _ = self._report_sched(); nxt = self.next_report_at(time.time() + 60)
        return (f"📅 Auto report · every {every} day{'s' if every != 1 else ''} at {hh:02d}:{mm:02d} IST · PAPER\n\n" + self.report_text()
                + "\n\n📂 Open positions\n" + self.positions_text()
                + (f"\n\nNext report: {dt.datetime.fromtimestamp(nxt, IST):%a %d %b %H:%M} IST" if nxt else "")
                + "\nPaper trading only - real trading can lose money.")

    async def report_loop(self):
        while True:
            try:
                key = self.report_due()
                if key:
                    self.store.put("auto_report_last", key)          # mark first: never double-send after a crash/restart
                    self.notify(self.auto_report_text()); log.info("auto report sent for %s", key)
            except Exception as e: log.warning("report loop: %s", e)
            await asyncio.sleep(30)

    async def _entry_gate(self, sym, d):
        s = self.cfg["strategy"]; now = time.time()
        if not self.running: return False, "paused"
        if sym not in self.cfg["enabled_symbols"]: return False, "symbol off"
        if sym in self.suspended: return False, f"BULK status {self.suspended[sym]}"
        if self.feed.proxy: return False, f"data proxy: {self.feed.proxy}"
        ok, why = self.pf.can_open(sym, now)
        if not ok: return False, why
        if self.cooldown.get(sym, 0) > now: return False, f"cooldown {int(self.cooldown[sym] - now)}s"
        if int(now // 3600 % 24) in set(s.get("skip_hours_utc") or []): return False, "skip hour"
        f = self.feed.funding.get(sym)
        if f and abs(f["rate"]) * 100 >= float(s["funding_skip_abs_pct"]) and 0 <= f["next"] - now <= 60 * float(s["funding_skip_min"]):
            return False, f"funding {f['rate'] * 100:.3f}% due in {int((f['next'] - now) / 60)}m"
        px = self.px(sym)
        if not px or self.feed.age(sym) > float(self.cfg["data"]["stale_after_sec"]): return False, "no live price"
        if (px - d["price"]) * d["side"] > 0.5 * d["stop_dist"]: return False, "price ran away since bar close"
        return True, "ok"

    async def funding_loop(self):
        while True:
            try:
                now = time.time()
                async with self.lock:
                    for sym, f in list(self.feed.funding.items()):
                        key = str(int(f["next"]))
                        if now >= f["next"] and self.funding_done.get(sym) != key:
                            self.funding_done[sym] = key
                            if sym in self.pf.positions:
                                self.pf.apply_funding(sym, f["rate"], self.marks().get(sym) or self.pf.positions[sym].entry, now)
                if now - getattr(self, "_fr", 0) > float(self.cfg["data"]["funding_refresh_sec"]) or any(now >= f["next"] for f in self.feed.funding.values()):
                    self._fr = now; await self.feed.refresh_funding()
            except Exception as e: log.warning("funding loop: %s", e)
            await asyncio.sleep(15)

    # ---------------- manual (paper) ----------------
    async def manual_preview(self, sym, side, risk_pct=None):
        if self.is_meme():
            raise ManualError("manual perp orders need a futures strategy (/set strategy.name hf_maker)", 409)
        sym = (sym or "").upper().replace("USDT", "")
        if sym not in self.cfg["symbols"]: raise ManualError(f"unknown symbol, use one of {', '.join(self.cfg['symbols'])}", 422)
        if sym in self.suspended: raise ManualError(f"BULK {sym}-USD is {self.suspended[sym]} - not tradable", 409)
        if side not in (1, -1): raise ManualError("side must be long or short", 422)
        risk_pct = float(risk_pct if risk_pct is not None else self.cfg["manual"]["default_risk_pct"])
        if not 0.1 <= risk_pct <= 2: raise ManualError("risk must be 0.1-2% of equity", 422)
        px = self.px(sym)
        if not px or self.feed.age(sym) > float(self.cfg["data"]["stale_after_sec"]): raise ManualError("no fresh price for this symbol right now", 503)
        trend = self.is_dc()
        eb = await self.feed.candles(sym, self.dcfg().get("tf", "1h") if trend else self.cfg["strategy"]["entry_tf"], 60)
        a = ind.atr([b[2] for b in eb], [b[3] for b in eb], [b[4] for b in eb], 14)[-1]
        if trend: stop_dist = float(self.dcfg().get("stop_atr", 3.0)) * a
        else: stop_dist = max(float(self.cfg["manual"]["default_stop_atr_mult"]) * a, float(self.cfg["strategy"]["min_stop_pct"]) / 100 * px)
        eq = self.equity(); qty, capped = self.pf.size(px, stop_dist, eq, risk_pct, symbol=sym)
        lev = self.pf.lev(sym); entry = px
        ok, why = self.pf.can_open(sym, time.time(), manual=True)
        if ok and self.feed.proxy: ok, why = False, f"data proxy: {self.feed.proxy}"
        if ok and qty * px < self.pf.min_notional(sym): ok, why = False, f"size ${qty * px:.0f} below BULK min notional ${self.pf.min_notional(sym):.0f}"
        await self.refresh_books([sym])
        slip = self.pf.ex.slippage(sym, "buy" if side > 0 else "sell", qty * px, px)
        return {"symbol": sym, "side": side, "price": px, "mark": self.feed.mark.get(sym), "stop": entry - side * stop_dist,
                "tp1": None if trend else entry + side * stop_dist * float(self.cfg["exits"]["tp1_r"]), "qty": qty, "mode": "trend" if trend else "scalp", "atr": a, "notional": qty * px, "margin": qty * px / lev, "lev": lev,
                "liq": liq_price(entry, side, lev, float(self.cfg["risk"]["maint_margin_pct"]) / 100), "risk_usd": qty * (stop_dist + 2 * self.pf.ex.taker * px),
                "risk_pct": risk_pct, "capped_by_leverage": capped, "equity": eq, "allowed": ok, "why": why,
                "est_fees": qty * px * self.pf.ex.taker * 2, "est_slippage_pct": slip * 100, "min_notional": self.pf.min_notional(sym),
                "signal": self.signals.get(sym, {}).get("reason")}

    async def manual_open(self, sym, side, risk_pct=None):
        pv = await self.manual_preview(sym, side, risk_pct)
        if not pv["allowed"]: raise ManualError(pv["why"], 409)
        async with self.lock:
            if pv["symbol"] in self.pf.positions: raise ManualError("already in a position on this symbol", 409)
            px = self.px(pv["symbol"]) or pv["price"]
            p = self.pf.open(pv["symbol"], side, px, abs(pv["price"] - pv["stop"]), time.time(), setup="manual", reason="manual entry", manual=True,
                             qty=pv["qty"], equity=self.equity(), atr=pv.get("atr"), mode=pv.get("mode", "scalp"), trail_mult=float(self.dcfg().get("trail_atr", 3.0)))
        if not p: raise ManualError("order below BULK min notional / size increment", 409)
        return {"ok": True, "position": p.to_dict()}

    async def manual_close(self, sym, pct=100):
        sym = (sym or "").upper().replace("USDT", "")
        pct = float(pct)
        if not 1 <= pct <= 100: raise ManualError("pct must be 1-100", 422)
        async with self.lock:
            p = self.pf.positions.get(sym)
            if not p: raise ManualError("no open position on " + sym, 404)
            px = self.px(sym)
            if not px: raise ManualError("no live price", 503)
            await self.refresh_books([sym])
            ev = self.pf.close(sym, pct / 100, px, time.time(), f"manual close {pct:g}%")
        return {"ok": True, "closed": ev["type"] == "close", "pnl": ev["pnl"], "price": ev["price"], "qty": ev["qty"], "symbol": sym}

    async def handle(self, kind, payload):
        if kind == "preview": return await self.manual_preview(payload.get("symbol"), int(payload.get("side", 0)), payload.get("risk_pct"))
        if kind == "open": return await self.manual_open(payload.get("symbol"), int(payload.get("side", 0)), payload.get("risk_pct"))
        if kind == "close": return await self.manual_close(payload.get("symbol"), payload.get("pct", 100))
        if kind == "close_all":
            out = [await self.manual_close(s, 100) for s in list(self.pf.positions)]; return {"ok": True, "closed": len(out)}
        raise ManualError("unknown request", 400)

    def pause(self): self.running = False; self.save()
    def resume(self):
        """Turns entries back on and clears the consecutive-loss breaker. The daily loss cap is NOT cleared (resets at IST midnight)."""
        self.running = True; b = self.pf.breakers
        if b.tripped and not b.tripped.startswith("daily"): b.tripped = None
        b.consec = 0; self.save()
        return b.tripped

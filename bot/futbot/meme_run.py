"""Scan loop for the paper gmgn_meme strategy. No Telegram import, no signing."""
import logging, os, time
from . import meme_rules
from .meme_paper import MemeBook
from .meme_sources import MarketData, _addr, assemble

log = logging.getLogger("meme")


def _row_public(facts, decision, checked, mode=None):
    return {
        "mint": facts.get("mint"), "symbol": facts.get("symbol"),
        "decision": decision["decision"], "reasons": decision["reasons"][:8],
        "gates_ok": bool(decision.get("gates_ok")),
        "mode": mode or decision.get("mode"),
        "gates": [{k: x.get(k) for k in ("code", "ok", "detail", "verified_by", "skipped")} for x in decision["gates"]],
        "price": facts.get("price"), "mcap": facts.get("mcap"), "liquidity": facts.get("liquidity"),
        "volume": facts.get("volume"), "volume_window": facts.get("volume_window"),
        "age_min": None if facts.get("age_sec") is None else round(facts["age_sec"] / 60.0, 1),
        "chg_1h": facts.get("chg_1h"), "chg_5m": facts.get("chg_5m"),
        "smart_n": len(set(facts["smart_buyers"])) if isinstance(facts.get("smart_buyers"), list) else facts.get("smart_degen_count"),
        "smart_note": (facts.get("sources") or {}).get("smart_money"),
        "score": decision.get("score"), "dev_rug_rate": decision.get("dev_rug_rate"),
        "dev_alive": decision.get("dev_alive"), "dev_analyzed": decision.get("dev_analyzed"),
        "checked": checked, "sources": facts.get("sources") or {},
    }


class Runner:
    def __init__(self, engine, data=None):
        self.eng = engine
        self.data = data or MarketData(engine.cfg)
        self.book = MemeBook(engine.cfg, engine.store, on_event=engine._event)
        self.scanner = list(engine.store.get("meme_scanner") or [])
        self._last_log = {}
        self._smart = set()
        self._smart_at = 0.0
        self._prints = {}
        self._confirm_at = {}

    def g(self):
        return (self.eng.cfg.get("strategy") or {}).get("gmgn") or {}

    def scan_once(self, limit=None):
        g = self.g()
        limit = int(limit or g.get("discover_limit", 20))
        enrich_n = int(g.get("enrich_per_scan", 8))
        mode = "nokey" if not self.data.gmgn.enabled else "gmgn"
        by_mint = {}
        if mode == "gmgn":
            rug_n = int(g.get("rugcheck_per_scan", 5))
            for row in self.data.gmgn_rank(limit):
                m = _addr(row)
                if m:
                    by_mint[m] = row
            if len(by_mint) < limit:
                for m in self.data.discover_dex(limit):
                    by_mint.setdefault(m, None)
        else:
            rug_n = int(g.get("nokey_rugcheck_per_scan", max(limit, 8)))
            for m in self.data.discover_nokey(limit):
                by_mint.setdefault(m, None)
        mints = list(by_mint)[:limit]
        pairs = self.data.dex_pairs(mints)
        if mode == "gmgn" and self.data.gmgn.enabled and time.time() - self._smart_at > 600:
            self._smart = self.data.gmgn_smart_set()
            self._smart_at = time.time()
        rows = []
        now = time.time()
        rug_net = 0
        for i, mint in enumerate(mints):
            info = security = pool = traders = created = rug = None
            if mode == "gmgn" and i < enrich_n and self.data.gmgn.enabled:
                en = self.data.gmgn_enrich(mint)
                info, security, pool, traders = en["info"], en["security"], en["pool"], en["traders"]
                creator = None
                if isinstance(info, dict) and isinstance(info.get("dev"), dict):
                    creator = info["dev"].get("creator_address")
                if creator:
                    created = self.data.gmgn_created(creator)
            if mode == "gmgn" and i < rug_n and g.get("rugcheck", True):
                rug = self.data.rugcheck(mint)
            facts = assemble(mint, pairs.get(mint), by_mint.get(mint), info, security, pool, traders,
                             self._smart, created, rug)
            if mode == "nokey" and g.get("rugcheck", True) and meme_rules.market_ready(facts, g):
                # Only spend a free RugCheck call when DexScreener gates can still pass.
                if self.data.rug_cached(mint) or rug_net < rug_n:
                    before = self.data.rug_fetches
                    rug = self.data.rugcheck(mint)
                    if self.data.rug_fetches > before:
                        rug_net += 1
                    if rug:
                        facts = assemble(mint, pairs.get(mint), by_mint.get(mint), info, security, pool, traders,
                                         self._smart, created, rug)
            decision = meme_rules.evaluate(facts, g, mode=mode)
            meme_rules.stamp_quote(facts, now, self._prints)
            q_ok, q_why = meme_rules.quote_entry_block(facts, g, now)
            if decision["ok"] and not q_ok:
                decision = dict(decision)
                decision["ok"] = False
                decision["decision"] = "reject"
                decision["entry_ok"] = False
                decision["reasons"] = list(decision["reasons"]) + [q_why]
            ready, c_why = self._confirm(mint, bool(decision["ok"]), now)
            if decision["ok"] and not ready:
                decision = dict(decision)
                decision["reasons"] = list(decision["reasons"]) + [c_why]
            pub = _row_public(facts, decision, now, mode=mode)
            pub["rule_pass"] = bool(decision["ok"])
            pub["confirmed"] = bool(ready)
            pub["metrics"] = meme_rules.entry_metrics(facts)
            rows.append(pub)
            self._log_reject(pub)
            if ready and self.eng.running:
                self._try_open(facts, decision, now)
            elif decision["ok"] and self.eng.running:
                self.eng.store.signal_log(facts.get("symbol") or "?", 1, "gmgn_meme",
                                          f"{mint} | {c_why} | {pub['metrics']}", "confirm")
        self.scanner = rows
        self.eng.store.put("meme_scanner", rows[-40:])
        for pub in rows:
            self.eng.store.signal(pub["mint"], pub)
        self._roll_breakers(now)
        self.book.persist()
        log.info("meme scan mode=%s %d tokens gmgn=%s dex=%s rug=%s open=%d",
                 "no-key" if mode == "nokey" else "gmgn",
                 len(rows), self.data.status.get("gmgn"), self.data.status.get("dexscreener"),
                 self.data.status.get("rugcheck"), len(self.book.positions))
        return rows

    def _log_reject(self, pub):
        if pub["decision"] == "enter":
            return
        key = (pub["mint"], tuple(pub["reasons"][:4]))
        if self._last_log.get(pub["mint"]) == key and time.time() - self._last_log.get(pub["mint"] + ":t", 0) < 600:
            return
        self._last_log[pub["mint"]] = key
        self._last_log[pub["mint"] + ":t"] = time.time()
        why = "; ".join(pub["reasons"])[:400]
        self.eng.store.signal_log(pub.get("symbol") or "?", 0, "gmgn_meme", f"{pub['mint']} | {why}", "reject")

    def _try_open(self, facts, decision, now):
        ok, why = self.book.can_open(facts.get("mint"), now)
        if not ok:
            self.eng.store.signal_log(facts.get("symbol") or "?", 1, "gmgn_meme", f"{facts.get('mint')} | skipped: {why}", "skipped")
            return
        pos, msg = self.book.open_pos(facts, decision["entry"], now)
        if pos:
            metrics = pos.get("entry_metrics") or meme_rules.entry_metrics(facts)
            self.eng.store.signal_log(pos["symbol"], 1, "gmgn_meme",
                                      f"{pos['mint']} | {decision['entry']} | {metrics}", "opened")
            payload = {"mint": pos["mint"], "symbol": pos["symbol"], "metrics": metrics,
                       "entry": pos["entry"], "quote_ts": facts.get("quote_ts"), "opened": now}
            self.eng.store.signal(pos["mint"], payload)
            if pos.get("symbol"):
                self.eng.store.signal(pos["symbol"], payload)
            log.info("paper open %s %s %s", pos["symbol"], pos["mint"], metrics)
        else:
            self.eng.store.signal_log(facts.get("symbol") or "?", 1, "gmgn_meme", f"{facts.get('mint')} | {msg}", "skipped")

    def _confirm(self, mint, passed, now):
        """Two passing scans at least confirm_gap_sec apart. A miss resets the pair."""
        gap = float(self.g().get("confirm_gap_sec", 30))
        if not passed:
            self._confirm_at.pop(mint, None)
            return False, "confirmation reset"
        first = self._confirm_at.get(mint)
        if first is None:
            self._confirm_at[mint] = now
            return False, "confirmation 1/2"
        if now - float(first) < gap:
            return False, f"confirmation waiting {gap - (now - float(first)):.0f}s"
        self._confirm_at.pop(mint, None)
        return True, "confirmation 2/2"

    def _roll_breakers(self, now):
        eq = self.book.equity()
        b = self.book.breakers
        b.roll(now, eq)
        before = b.tripped
        b.check_daily(eq)
        if b.tripped and b.tripped != before and str(b.tripped).startswith("daily"):
            days = self.eng.store.get("daily_cap_days") or []
            from .portfolio import ist_day
            if ist_day(now) not in days:
                self.eng.store.put("daily_cap_days", (days + [ist_day(now)])[-400:])
            self.eng.notify(f"🛑 Memecoin circuit breaker: {b.tripped}. No new paper entries.")
        if now - getattr(self, "_eq_at", 0) > 300:
            self._eq_at = now
            self.eng.store.equity(now, eq, self.book.balance)

    def tick(self):
        """Price open paper positions and apply exits. One DexScreener batch."""
        now = time.time()
        mints = list(self.book.positions)
        pairs = self.data.dex_pairs(mints) if mints else {}
        stale_after = float(self.g().get("stale_data_sec", 90))
        exit_after = float(self.g().get("stale_exit_sec", 180))
        max_quote = float(self.g().get("max_quote_age_sec", 30))
        priced = 0
        for mint in mints:
            p = self.book.positions.get(mint)
            if not p:
                continue
            pair = pairs.get(mint)
            from .meme_sources import from_dex_pair
            got = from_dex_pair(pair) if pair else {}
            if got:
                meme_rules.stamp_quote(got, now, self._prints)
            px, liq = got.get("price"), got.get("liquidity")
            q_ts = got.get("quote_ts")
            fresh_quote = q_ts is not None and (now - float(q_ts)) <= max_quote
            if px and fresh_quote:
                priced += 1
                self.book.mark(mint, px, liq, now, stale=False)
            else:
                age = now - float((self.book.positions.get(mint) or p).get("mark_ts") or 0)
                if age > exit_after and mint in self.book.positions:
                    last = self.book.positions[mint].get("mark")
                    self.book.mark(mint, last, self.book.positions[mint].get("liquidity"), now, stale=True)
        fresh = priced == len(mints) and (not mints or priced > 0)
        # entries pause when the batch that should have priced open positions missed, or the last scan is old
        scan_age = now - max((r.get("checked") or 0) for r in self.scanner) if self.scanner else 1e9
        if mints and priced < len(mints):
            self.book.breakers.stale = f"stale data: {len(mints) - priced} open token(s) missing a price"
        elif scan_age > max(stale_after, float(self.g().get("poll_sec", 25)) * 3):
            self.book.breakers.stale = f"stale data: scanner age {int(scan_age)}s"
        else:
            self.book.breakers.stale = None
        self._roll_breakers(now)
        self.book.persist()
        return fresh

    def positions_public(self):
        now = time.time()
        out = []
        for p in self.book.positions.values():
            mk = p.get("mark") or p["entry"]
            cost_left = float(p.get("cost") or 0) * (float(p["qty"]) / float(p["qty0"])) if p.get("qty0") else 0
            upnl = mk * float(p["qty"]) - cost_left
            out.append({
                **p, "mark": mk, "upnl": upnl,
                "upnl_pct_margin": (upnl / p["margin"] * 100.0) if p.get("margin") else 0,
                "last": mk, "held_min": (now - p["opened"]) / 60.0,
            })
        return out

    def snapshot(self):
        now = time.time()
        b = self.book.breakers
        eq = self.book.equity()
        g = self.g()
        return {
            "heartbeat": now, "running": self.eng.running, "breaker": b.tripped, "stale": b.stale,
            "consec_losses": b.consec, "day_start_eq": b.day_start_eq, "balance": self.book.balance,
            "equity": eq, "used_margin": sum(p.get("margin") or 0 for p in self.book.positions.values()),
            "source": self.data.mode_label(),
            "sources": dict(self.data.status), "proxy": None, "venue": "SOL", "ws": False,
            "suspended": {}, "rules": {}, "prices": {}, "funding": {},
            "positions": self.positions_public(), "signals": {r["mint"]: r for r in self.scanner if r.get("mint")},
            "scanner": self.scanner, "stats": {}, "enabled_symbols": [], "symbols": [],
            "max_open": int(g.get("max_open_positions", 4)),
            "strategy": self.eng.strategy_info(), "quotes": {}, "pauses": b.pauses, "pause": b.paused(),
            "history_source": {}, "last_signal_run": max((r.get("checked") or 0) for r in self.scanner) if self.scanner else None,
        }

    def positions_text(self):
        ps = self.positions_public()
        if not ps:
            return "No open memecoin positions."
        lines = []
        for p in ps:
            gain = (p["mark"] / p["entry"] - 1) * 100 if p["entry"] else 0
            lines.append(
                f"🟢 {p['symbol']} ({str(p['mint'])[:4]}…{str(p['mint'])[-4:]})\n"
                f"Entry ${p['entry']:.6g} · now ${p['mark']:.6g} · {gain:+.1f}% · uPnL {p['upnl']:+.2f} USD\n"
                f"Stop ${p['stop']:.6g} · liq ${p.get('liquidity') or 0:,.0f} · held {p['held_min']:.0f}m\n"
                f"Paper size ${p['notional0']:.2f} · fees ${p['fees']:.2f}"
            )
        return "\n\n".join(lines)

    async def loop(self):
        import asyncio
        while True:
            if not self.eng.is_meme():
                await asyncio.sleep(2)
                continue
            try:
                await asyncio.to_thread(self.scan_once)
            except Exception as e:
                log.warning("scan: %s", str(e)[:160])
            await asyncio.sleep(float(self.g().get("poll_sec", 25)))

    async def price_loop(self):
        import asyncio
        while True:
            if not self.eng.is_meme():
                await asyncio.sleep(1)
                continue
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:
                log.warning("tick: %s", str(e)[:160])
            if self.eng.data_dir:
                try:
                    with open(os.path.join(self.eng.data_dir, "heartbeat"), "w") as f:
                        f.write(str(time.time()))
                except Exception:
                    pass
            await asyncio.sleep(float(self.g().get("price_poll_sec", 5)))


def scan_and_print(cfg, limit=8):
    """One public scan. Prints no secrets. Used by the dry-run script."""
    from .store import Store
    import tempfile
    class _E:
        running = False
        def __init__(self):
            self.cfg = cfg
            self.store = Store(os.path.join(tempfile.mkdtemp(), "dry.db"))
            self.data_dir = None
            self.started = time.time()
        def _event(self, e):
            pass
        def notify(self, m):
            pass
        def is_meme(self):
            return True
        def strategy_info(self):
            return {"name": "gmgn_meme", "label": "dry-run", "since": self.started}
    eng = _E()
    data = MarketData(cfg)
    run = Runner(eng, data)
    rows = run.scan_once(limit=limit)
    print(f"gmgn_meme dry-run · paper · mode {data.mode_label()} · no telegram · tokens {len(rows)}")
    print("sources:", data.status)
    if data.mode == "nokey":
        print("no-key mode: DexScreener + RugCheck. Dev history, bundlers, snipers, and smart-money are skipped. "
              "Honeypot uses RugCheck risk flags. Missing data on a remaining gate rejects.")
    print(f"{'symbol':<12} {'decision':<8} {'gates':<6} reasons")
    for r in rows:
        shown = list(r["reasons"][:2])
        if r["reasons"] and shown and all("unknown" in x.lower() for x in shown) and r["reasons"][-1] not in shown:
            shown.append(r["reasons"][-1])
        why = "; ".join(shown)
        print(f"{str(r['symbol'])[:12]:<12} {r['decision']:<8} {'pass' if r.get('gates_ok') else 'fail':<6} {why[:180]}")
    gates_ok = [r for r in rows if r.get("gates_ok")]
    enters = [r for r in rows if r["decision"] == "enter"]
    confirmed = [r for r in rows if r.get("confirmed")]
    print(f"gates_passed {len(gates_ok)} / {len(rows)}")
    print(f"rule_pass {len(enters)} / {len(rows)}")
    print(f"confirmed_entries {len(confirmed)} / {len(rows)} (a live entry needs a second passing scan at least "
          f"{int((cfg.get('strategy') or {}).get('gmgn', {}).get('confirm_gap_sec', 30))}s later)")
    print(f"entries {len(enters)} / {len(rows)} (fail-closed). A DexScreener price is not a sell proof.")
    return rows

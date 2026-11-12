"""Paper book for gmgn_meme. No wallet, no signing.

Fills are a model, not a chain simulation: AMM-style percent fee + a flat SOL-priority
estimate + slippage that grows with notional/liquidity and never below a floor.
Missing liquidity refuses the fill (fail-closed). Sell-side also applies a known sell tax.
A fill price is not proof the token can be sold on-chain.
"""
import time
from .meme_rules import entry_metrics
from .portfolio import Breakers


def slippage_pct(notional, liquidity, floor_pct, impact_k, cap_pct):
    """Percent points. None when liquidity is missing or not positive."""
    if liquidity is None or liquidity <= 0 or notional is None or notional <= 0:
        return None
    impact = float(impact_k) * (float(notional) / float(liquidity)) * 100.0
    raw = max(float(floor_pct), impact)
    return min(float(cap_pct), raw)


def buy_fill(price, notional, liquidity, fee_pct, flat_fee, floor_pct, impact_k, cap_pct):
    """Spend `notional` quote. Returns dict or None if the fill cannot be priced."""
    if price is None or price <= 0 or notional is None or notional <= 0:
        return None
    slip = slippage_pct(notional, liquidity, floor_pct, impact_k, cap_pct)
    if slip is None:
        return None
    fee = float(notional) * float(fee_pct) / 100.0 + float(flat_fee)
    if fee >= float(notional):
        return None
    exec_px = float(price) * (1 + slip / 100.0)
    qty = (float(notional) - fee) / exec_px
    if qty <= 0:
        return None
    return {"side": "buy", "price": exec_px, "qty": qty, "fee": fee, "slippage_pct": slip,
            "notional": float(notional), "ref": float(price)}


def sell_fill(price, qty, liquidity, fee_pct, flat_fee, floor_pct, impact_k, cap_pct, sell_tax=0.0, haircut_pct=0.0):
    """Sell `qty` base. sell_tax is a fraction (0.05 = 5%). haircut_pct adds worse slippage (stale exit)."""
    if price is None or price <= 0 or qty is None or qty <= 0:
        return None
    notional_guess = float(qty) * float(price)
    slip = slippage_pct(notional_guess, liquidity, floor_pct, impact_k, cap_pct)
    if slip is None:
        return None
    slip = min(float(cap_pct), slip + float(haircut_pct or 0))
    tax = float(sell_tax or 0)
    if tax < 0 or tax >= 1:
        return None
    gross = float(qty) * float(price) * (1 - slip / 100.0) * (1 - tax)
    fee = gross * float(fee_pct) / 100.0 + float(flat_fee)
    proceeds = gross - fee
    if proceeds <= 0:
        return None
    return {"side": "sell", "price": float(price) * (1 - slip / 100.0) * (1 - tax), "qty": float(qty),
            "fee": fee, "slippage_pct": slip, "proceeds": proceeds, "ref": float(price), "sell_tax": tax}


class MemeBook:
    def __init__(self, cfg, store, on_event=None):
        self.cfg = cfg
        self.g = (cfg.get("strategy") or {}).get("gmgn") or {}
        self.store = store
        self.on_event = on_event or (lambda e: None)
        start = float(cfg["paper"]["start_balance"])
        self.balance = float(store.get("meme_balance", start))
        self.positions = dict(store.get("meme_positions") or {})
        risk = {
            "daily_loss_cap_pct": float(self.g.get("daily_loss_cap_pct", 6)),
            "max_consecutive_losses": int(self.g.get("max_consecutive_losses", 3)),
            "consec_reset_daily": True,
        }
        self.breakers = Breakers(risk)
        self.breakers.load(store.get("meme_breakers") or {})
        self.cooldowns = dict(store.get("meme_reentry") or {})
        self.closed = []

    def gnum(self, k, d):
        return float(self.g.get(k, d))

    def equity(self):
        """Cash plus mark value of tokens. Entry spends cash; the tokens are still equity."""
        return self.balance + sum(float(p.get("mark") or p["entry"]) * float(p["qty"]) for p in self.positions.values())

    def persist(self):
        self.store.put("meme_balance", self.balance)
        self.store.put("meme_positions", self.positions)
        self.store.put("meme_breakers", self.breakers.to_dict())
        self.store.put("meme_reentry", self.cooldowns)

    def can_open(self, mint, now=None):
        now = now or time.time()
        if mint in self.positions:
            return False, "already in this token"
        until = self.cooldowns.get(mint)
        if until and now < float(until):
            return False, f"re-entry cooldown {int(float(until) - now)}s"
        b = self.breakers.blocked(now)
        if b:
            return False, f"breaker: {b}"
        mo = int(self.g.get("max_open_positions", 4))
        if len(self.positions) >= mo:
            return False, f"max {mo} open positions"
        return True, "ok"

    def _arm_cooldown(self, mint, reason, now):
        text = str(reason or "")
        stopped = text.startswith("stop loss") or text.startswith("stop (raised)")
        sec = self.gnum("stop_reentry_cooldown_sec", 86400) if stopped else self.gnum("reentry_cooldown_sec", 21600)
        self.cooldowns[mint] = float(now) + sec

    def _fills(self):
        return (self.gnum("fee_pct_per_side", 0.30), self.gnum("flat_fee_usd", 0.15),
                self.gnum("slippage_floor_pct", 0.80), self.gnum("slippage_impact_k", 1.0),
                self.gnum("slippage_cap_pct", 8))

    def open_pos(self, facts, reason, now=None):
        now = now or time.time()
        mint = facts.get("mint")
        ok, why = self.can_open(mint, now)
        if not ok:
            return None, why
        eq = self.equity()
        pct = min(2.0, max(1.0, self.gnum("position_size_pct", 1.5)))
        notional = eq * pct / 100.0
        if notional > self.balance:
            return None, "not enough paper cash"
        fee_pct, flat, floor, impact, cap = self._fills()
        fill = buy_fill(facts.get("price"), notional, facts.get("liquidity"), fee_pct, flat, floor, impact, cap)
        if not fill:
            return None, "no paper fill (missing price or liquidity)"
        stop_pct = self.gnum("stop_loss_pct", 10) / 100.0
        entry = fill["price"]
        stop = entry * (1 - stop_pct)
        tps = list(self.g.get("take_profit") or [[25, 40], [60, 30], [120, 30]])
        self.balance -= notional  # cash out; proceeds return on sells. fee is inside notional
        pos = {
            "id": f"{(facts.get('symbol') or 'tok')}-{int(now)}",
            "symbol": facts.get("symbol") or mint[:6],
            "mint": mint,
            "side": 1,
            "entry": entry,
            "ref": fill["ref"],
            "qty": fill["qty"],
            "qty0": fill["qty"],
            "stop": stop,
            "stop0": stop,
            "tp_done": [],
            "take_profit": tps,
            "opened": now,
            "mark": entry,
            "mark_ts": now,
            "liq0": facts.get("liquidity"),
            "liquidity": facts.get("liquidity"),
            "mcap": facts.get("mcap"),
            "sell_tax": float(facts.get("sell_tax") or 0),
            "fees": fill["fee"],
            "cost": notional,
            "realized": 0.0,  # fee is already inside `cost` (fewer tokens bought)
            "reason": reason,
            "entry_metrics": entry_metrics(facts),
            "quote_ts": facts.get("quote_ts"),
            "setup": "gmgn_meme",
            "manual": False,
            "mode": "meme",
            "lev": 1,
            "margin": notional,
            "notional0": notional,
            "slippage_pct": fill["slippage_pct"],
            "funding": 0.0,
            "r": entry - stop,
            "tp1": entry * (1 + float(tps[0][0]) / 100.0) if tps else None,
            "tp1_done": False,
            "liq": None,
        }
        self.positions[mint] = pos
        self.on_event({"type": "open", "ts": now, "pos": pos, "fee": fill["fee"]})
        self.persist()
        return pos, "opened"

    def _sell(self, mint, qty, price, liquidity, now, reason, haircut=0.0):
        p = self.positions.get(mint)
        if not p:
            return None
        qty = min(float(qty), p["qty"])
        if qty <= 0:
            return None
        if p["qty"] - qty < p["qty0"] * 0.02:
            qty = p["qty"]
        fee_pct, flat, floor, impact, cap = self._fills()
        # scale flat fee by fraction so a 3-step TP is not charged 3 full tips on a tiny book... still charge flat each fill;
        # that is harsher and closer to a real priority fee per transaction.
        fill = sell_fill(price, qty, liquidity if liquidity is not None else p.get("liquidity"),
                         fee_pct, flat, floor, impact, cap, p.get("sell_tax") or 0, haircut)
        if not fill:
            return None
        cost_part = p["cost"] * (qty / p["qty0"]) if p["qty0"] else 0
        pnl = fill["proceeds"] - cost_part
        p["qty"] -= qty
        p["fees"] += fill["fee"]
        p["realized"] += pnl
        self.balance += fill["proceeds"]
        full = p["qty"] <= p["qty0"] * 1e-9
        ev = {"type": "close" if full else "partial", "ts": now, "symbol": p["symbol"], "mint": mint,
              "qty": qty, "price": fill["price"], "fee": fill["fee"], "pnl": pnl, "reason": reason, "pos": dict(p)}
        if full:
            metrics = p.get("entry_metrics") or ""
            trade_reason = f"{reason} | {metrics}" if metrics else reason
            trade = {"id": p["id"], "symbol": p["symbol"], "side": 1, "entry": p["entry"], "exit": fill["price"],
                     "qty": p["qty0"], "notional": p["notional0"], "lev": 1, "opened": p["opened"], "closed": now,
                     "pnl": p["realized"], "fees": p["fees"], "funding": 0.0,
                     "r_mult": (p["realized"] / (p["r"] * p["qty0"])) if p.get("r") and p["qty0"] else 0,
                     "reason": trade_reason, "setup": "gmgn_meme", "manual": 0, "tp1": bool(p.get("tp_done")),
                     "entry_metrics": metrics}
            self.closed.append(trade)
            ev["trade"] = trade
            ev["reason"] = trade_reason
            self._arm_cooldown(mint, reason, now)
            del self.positions[mint]
            self.breakers.on_close(trade["pnl"], now)
        self.on_event(ev)
        self.persist()
        return ev

    def mark(self, mint, price, liquidity, now, stale=False):
        """Update one open position. Returns a list of exit reasons applied."""
        p = self.positions.get(mint)
        if not p or price is None or price <= 0:
            return []
        p["mark"] = price
        p["mark_ts"] = now
        if liquidity is not None:
            p["liquidity"] = liquidity
        done = []
        # liquidity-drop breaker: exit, do not average down
        liq0 = p.get("liq0")
        drop = self.gnum("liquidity_drop_pct", 40)
        if liq0 and liquidity is not None and liquidity < liq0 * (1 - drop / 100.0):
            ev = self._sell(mint, p["qty"], price, liquidity, now, f"liquidity drop {liquidity / liq0 - 1:.0%}")
            if ev:
                done.append(ev["reason"])
            return done
        if stale:
            hair = self.gnum("stale_haircut_pct", 15)
            ev = self._sell(mint, p["qty"], price, p.get("liquidity"), now, "stale price exit", haircut=hair)
            if ev:
                done.append(ev["reason"])
            return done
        if price <= p["stop"]:
            kind = "stop loss" if abs(p["stop"] - p["stop0"]) / p["entry"] < 1e-6 else "stop (raised)"
            # Fill at the quoted price. A gap through the stop is not clipped back to -10%.
            thru = (p["stop"] - price) / p["entry"] * 100.0
            gap = f" gap {thru:.1f}pp through stop" if thru > 1 else ""
            ev = self._sell(mint, p["qty"], price, p.get("liquidity"), now,
                            f"{kind} {(price / p['entry'] - 1) * 100:+.1f}%{gap}")
            if ev:
                done.append(ev["reason"])
            return done
        held_min = (now - p["opened"]) / 60.0
        gain = (price / p["entry"] - 1) * 100.0
        if held_min >= self.gnum("max_hold_min", 120):
            ev = self._sell(mint, p["qty"], price, p.get("liquidity"), now, f"time stop max hold {held_min:.0f}m ({gain:+.1f}%)")
            if ev:
                done.append(ev["reason"])
            return done
        # At 25m: underwater vs the fill exits. In profit, stop moves to breakeven and the position can run to max hold.
        if held_min >= self.gnum("time_stop_min", 25) and not p.get("tp_done"):
            if price < p["entry"]:
                ev = self._sell(mint, p["qty"], price, p.get("liquidity"), now,
                                f"time stop below entry {held_min:.0f}m ({gain:+.1f}%)")
                if ev:
                    done.append(ev["reason"])
                return done
            if price > p["entry"]:
                be = p["entry"] * (1 + self.gnum("fee_pct_per_side", 0.30) / 100.0)
                if be > p["stop"]:
                    p["stop"] = be
                    self.persist()
        for i, spec in enumerate(p.get("take_profit") or []):
            if i in (p.get("tp_done") or []):
                continue
            lvl, portion = float(spec[0]), float(spec[1])
            if gain >= lvl:
                ev = self._sell(mint, p["qty0"] * portion / 100.0, price, p.get("liquidity"), now, f"TP{i + 1} {gain:+.1f}%")
                if not ev:
                    break
                done.append(ev["reason"])
                if mint not in self.positions:
                    break
                p = self.positions[mint]
                p.setdefault("tp_done", []).append(i)
                p["tp1_done"] = True
                if i == 0 and self.g.get("breakeven_after_tp1", True):
                    be = p["entry"] * (1 + self.gnum("fee_pct_per_side", 0.30) / 100.0)
                    if be > p["stop"]:
                        p["stop"] = be
                self.persist()
        return done

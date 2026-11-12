"""Paper futures book (USD-margined linear perps on BULK, isolated margin). Shared by backtest and live engine.

Position sizing: risk_pct of equity / stop distance, capped so that notional <= equity * leverage / max_open
(=> whole-account effective leverage never above `leverage`, max 3x). Fees/slippage go through the Executor.
Exits: stop, TP1 at tp1_r * R (close tp1_frac, stop -> breakeven + be_offset), ATR trail on the rest, optional TP2, max hold.
Breakers: daily loss cap (IST day), N consecutive losses, stale data (engine sets it)."""
import datetime as dt, math, time
from .execution import PaperExecutor

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_LEV = 3.0

def ist_day(ts): return dt.datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")

def liq_price(entry, side, lev, mmr=0.005):
    """Isolated-margin liquidation estimate for a linear perp (fees ignored)."""
    return entry * (1 - 1 / lev + mmr) if side > 0 else entry * (1 + 1 / lev - mmr)

class Position:
    __slots__ = ("id", "symbol", "side", "entry", "qty0", "qty", "stop", "stop0", "r", "tp1", "tp1_done", "tp2", "best", "trail_dist",
                 "opened", "lev", "liq", "margin", "fees", "funding", "realized", "setup", "manual", "reason", "mark", "notional0", "mode",
                 "hold_min", "maker_entry")
    def __init__(self, **k):
        for s in self.__slots__: setattr(self, s, k.get(s))
    def to_dict(self): return {s: getattr(self, s) for s in self.__slots__}
    @classmethod
    def from_dict(cls, d): return cls(**d)
    def upnl(self, mark=None):
        m = mark if mark is not None else (self.mark or self.entry)
        return (m - self.entry) * self.qty * self.side

class Breakers:
    """`ov` = per-strategy overrides of `risk:` (hf_maker: max_consecutive_losses / consec_pause_min). consec_pause_min > 0 turns
    the loss-streak breaker into a pause of that many minutes instead of blocking the rest of the IST day."""
    def __init__(self, r):
        self.r = r; self.ov = {}; self.day = None; self.day_start_eq = None; self.consec = 0; self.tripped = None; self.stale = None
        self.pause_until = 0; self.pauses = 0
    def g(self, k, d=None): return self.ov.get(k, self.r.get(k, d))
    def roll(self, ts, equity):
        d = ist_day(ts)
        if d != self.day:
            self.day, self.day_start_eq = d, equity
            if self.tripped and (self.tripped.startswith("daily") or ("consecutive" in self.tripped and self.r.get("consec_reset_daily", True))):
                self.tripped = None; self.consec = 0
    def on_close(self, pnl, ts=None):
        self.consec = self.consec + 1 if pnl < 0 else 0
        if self.consec >= int(self.g("max_consecutive_losses")) and not self.tripped:
            pm = float(self.g("consec_pause_min", 0) or 0)
            if pm > 0:
                self.pause_until = (ts or time.time()) + pm * 60; self.pauses += 1; self.consec = 0
            else: self.tripped = f"{self.consec} consecutive losses"
    def check_daily(self, equity):
        if self.day_start_eq and (self.day_start_eq - equity) / self.day_start_eq * 100 >= float(self.r["daily_loss_cap_pct"]):
            if not self.tripped or not self.tripped.startswith("daily"): self.tripped = f"daily loss cap {self.r['daily_loss_cap_pct']}% hit"
        return self.tripped
    def paused(self, now=None):
        if self.pause_until and (now or time.time()) < self.pause_until:
            return f"loss-streak pause until {dt.datetime.fromtimestamp(self.pause_until, IST):%H:%M} IST"
        return None
    def blocked(self, now=None): return self.tripped or self.stale or self.paused(now)
    def reset(self): self.tripped = None; self.consec = 0
    def to_dict(self): return {"day": self.day, "day_start_eq": self.day_start_eq, "consec": self.consec, "tripped": self.tripped,
                               "pause_until": self.pause_until, "pauses": self.pauses}
    def load(self, d):
        for k in ("day", "day_start_eq", "consec", "tripped", "pause_until", "pauses"):
            if k in (d or {}): setattr(self, k, d[k])

class Portfolio:
    def __init__(self, cfg, executor=None, balance=None, on_event=None):
        self.cfg = cfg; self.r = cfg["risk"]; self.x = cfg["exits"]
        self.ex = executor or PaperExecutor(cfg.get("fees") or {})
        self.balance = float(balance if balance is not None else cfg["paper"]["start_balance"])
        self.positions = {}; self.breakers = Breakers(self.r); self.on_event = on_event or (lambda e: None); self.seq = 0
        self.closed = []      # list of closed-trade dicts (backtest keeps all; live engine persists via on_event)
        self.hf_thr = 0.0001  # hf_maker: maker target fills only on a trade THROUGH the level by this fraction (engine sets it)
        self.rules = {}       # symbol -> BULK exchangeInfo {tickSize, sizeIncrement, minNotional, maxLeverage, status}

    @property
    def ov(self): return self.breakers.ov
    @ov.setter
    def ov(self, d): self.breakers.ov = dict(d or {})
    def rk(self, k, d=None): return self.breakers.g(k, d)

    # ---------- accounting ----------
    def lev(self, symbol=None):
        cap = float((self.rules.get(symbol) or {}).get("maxLeverage") or MAX_LEV)
        return max(1.0, min(MAX_LEV, cap, float(self.r.get("leverage", 3))))
    def min_notional(self, symbol): return float((self.rules.get(symbol) or {}).get("minNotional") or self.r.get("min_notional", 5))
    def round_qty(self, symbol, qty):
        step = float((self.rules.get(symbol) or {}).get("sizeIncrement") or 0)
        return math.floor(qty / step + 1e-9) * step if step else qty
    def round_px(self, symbol, px, up=None):
        tick = float((self.rules.get(symbol) or {}).get("tickSize") or 0)
        if not tick: return px
        f = math.ceil if up else math.floor if up is False else round
        return f(px / tick) * tick
    def equity(self, marks=None):
        return self.balance + sum(p.upnl((marks or {}).get(p.symbol)) for p in self.positions.values())
    def used_margin(self): return sum(p.margin * p.qty / p.qty0 for p in self.positions.values())

    def can_open(self, symbol, ts, manual=False):
        if symbol in self.positions: return False, "already in a position on this symbol"
        b = self.breakers.blocked(ts)
        if b: return False, f"breaker: {b}"
        mo = int(self.rk("max_open_positions"))
        if not manual and len(self.positions) >= mo: return False, f"max {mo} open positions"
        if manual and len(self.positions) >= mo + 1: return False, "too many open positions"
        return True, "ok"

    def size(self, price, stop_dist, equity, risk_pct=None, symbol=None):
        """qty such that a stop-out loses risk_pct of equity (fees on top), capped by leverage budget."""
        risk = equity * float(risk_pct if risk_pct is not None else self.rk("risk_pct")) / 100
        fee = self.ex.taker if hasattr(self.ex, "taker") else 0.0005
        qty = risk / (stop_dist + 2 * fee * price)          # include round-trip taker fees in the risk budget
        cap = equity * self.lev(symbol) / max(1, int(self.rk("max_open_positions"))) / price
        return self.round_qty(symbol, min(qty, cap)), qty > cap

    # ---------- open / close ----------
    def open(self, symbol, side, price, stop_dist, ts, atr=None, setup="", reason="", manual=False, qty=None, equity=None, mode="scalp", trail_mult=None,
             maker=False, target=None, hold_min=None):
        """mode 'scalp': TP1 -> breakeven -> ATR trail, max hold. mode 'trend' (Donchian): stop + chandelier trail on closed bars only
        (Portfolio.trail_bar), no take-profit, no max hold."""
        eq = equity if equity is not None else self.equity()
        if qty is None: qty, capped = self.size(price, stop_dist, eq, symbol=symbol)
        qty = self.round_qty(symbol, qty)
        if qty <= 0 or qty * price < self.min_notional(symbol): return None
        f = self.ex.limit_alo(symbol, "buy" if side > 0 else "sell", qty, price) if maker else self.ex.market(symbol, "buy" if side > 0 else "sell", qty, price)
        lev = self.lev(symbol); notional = f.price * qty
        stop = self.round_px(symbol, f.price - side * stop_dist, up=side < 0)
        r = abs(f.price - stop)
        self.seq += 1
        p = Position(id=f"{symbol}-{int(ts)}-{self.seq}", symbol=symbol, side=side, entry=f.price, qty0=qty, qty=qty, stop=stop, stop0=stop, r=r,
                     tp1=None if mode == "trend" else self.round_px(symbol, target) if mode == "hf" else self.round_px(symbol, f.price + side * r * float(self.x["tp1_r"]), up=side > 0), tp1_done=False,
                     tp2=(f.price + side * r * float(self.x["tp2_r"])) if mode not in ("trend", "hf") and float(self.x.get("tp2_r") or 0) > 0 else None,
                     best=f.price, mode=mode,
                     trail_dist=(float(trail_mult) * atr) if mode == "trend" and trail_mult and atr else float(self.x["trail_atr_mult"]) * (atr or r / max(1e-9, float(self.cfg["strategy"]["atr_stop_mult"]))),
                     opened=ts, lev=lev, liq=liq_price(f.price, side, lev, float(self.r.get("maint_margin_pct", 0.5)) / 100), margin=notional / lev,
                     fees=f.fee, funding=0.0, realized=-f.fee, setup=setup, manual=manual, reason=reason, mark=f.price, notional0=notional,
                     hold_min=hold_min, maker_entry=bool(maker))
        self.balance -= f.fee
        self.positions[symbol] = p
        self.on_event({"type": "open", "ts": ts, "pos": p.to_dict(), "fill": f.price, "fee": f.fee})
        return p

    def close(self, symbol, frac, price, ts, reason, maker=False):
        p = self.positions.get(symbol)
        if not p: return None
        qty = p.qty if frac >= 0.999 else self.round_qty(symbol, p.qty * frac)     # frac = fraction of what is still open
        if qty <= 0 or (p.qty - qty) * price < self.min_notional(symbol): qty = p.qty   # never leave an un-closable remainder
        qty = min(qty, p.qty)
        side = "sell" if p.side > 0 else "buy"
        f = self.ex.limit_alo(symbol, side, qty, price) if maker else self.ex.market(symbol, side, qty, price)
        pnl = (f.price - p.entry) * qty * p.side - f.fee
        p.qty -= qty; p.fees += f.fee; p.realized += pnl + 0  # realized includes entry fee (set at open) + funding
        self.balance += pnl
        full = p.qty <= p.qty0 * 1e-9
        ev = {"type": "close" if full else "partial", "ts": ts, "symbol": symbol, "qty": qty, "price": f.price, "fee": f.fee, "pnl": pnl, "reason": reason, "pos": p.to_dict()}
        if full:
            del self.positions[symbol]
            t = {"id": p.id, "symbol": symbol, "side": p.side, "entry": p.entry, "exit": f.price, "qty": p.qty0, "notional": p.notional0, "lev": p.lev,
                 "opened": p.opened, "closed": ts, "pnl": p.realized, "fees": p.fees, "funding": p.funding, "r_mult": p.realized / (p.r * p.qty0) if p.r else 0,
                 "reason": reason, "setup": p.setup, "manual": p.manual, "tp1": p.tp1_done}
            self.closed.append(t); ev["trade"] = t
            self.breakers.on_close(p.realized, ts)
        self.on_event(ev)
        return ev

    def apply_funding(self, symbol, rate, mark, ts):
        p = self.positions.get(symbol)
        if not p or not rate: return 0.0
        pay = p.side * p.qty * mark * rate          # longs pay positive funding
        p.funding -= pay; p.realized -= pay; self.balance -= pay
        self.on_event({"type": "funding", "ts": ts, "symbol": symbol, "amount": -pay, "rate": rate})
        return -pay

    # ---------- exits ----------
    def on_bar(self, symbol, o, h, l, c, ts):
        """Backtest path: process one 1m bar conservatively (stop before target inside the same bar)."""
        p = self.positions.get(symbol)
        if not p: return
        p.mark = c
        adverse, favorable = (l, h) if p.side > 0 else (h, l)
        def hit(level, px): return (px <= level) if p.side > 0 else (px >= level)
        def hit_up(level, px): return (px >= level) if p.side > 0 else (px <= level)
        # gap through stop / liq at open
        if hit(p.stop, o) or hit(p.liq, o):
            return self.close(symbol, 1, o, ts, self._stop_reason(p))
        if hit(p.liq, adverse) and not hit(p.stop, adverse):
            return self.close(symbol, 1, p.liq, ts, "liquidated")
        if hit(p.stop, adverse):
            return self.close(symbol, 1, p.stop, ts, self._stop_reason(p))
        if p.mode == "hf":
            if p.tp1 is not None and ((favorable > p.tp1 * (1 + self.hf_thr)) if p.side > 0 else (favorable < p.tp1 * (1 - self.hf_thr))):
                return self.close(symbol, 1, p.tp1, ts, "target (maker)", maker=True)
            return self._hf_time(p, c, ts)
        if p.tp1 is not None and not p.tp1_done and (hit_up(p.tp1, favorable) and (not self.x.get("tp1_maker") or favorable != p.tp1)):   # maker limit needs a trade THROUGH the price
            self._tp1(p, p.tp1, ts)
            if symbol not in self.positions: return
        if p.tp1_done and p.tp2 and hit_up(p.tp2, favorable):
            return self.close(symbol, 1, p.tp2, ts, "TP2")
        # trail on close-to-close extremes (updated after the bar so it never helps inside the same bar)
        if p.mode == "trend": return
        if p.tp1_done:
            p.best = max(p.best, h) if p.side > 0 else min(p.best, l)
            self._trail(p)
        else:
            p.best = max(p.best, h) if p.side > 0 else min(p.best, l)
        if (ts - p.opened) / 60 >= float(self.x["max_hold_min"]):
            return self.close(symbol, 1, c, ts, f"max hold {self.x['max_hold_min']}m")

    def on_price(self, symbol, px, ts):
        """Live path: every tick/mark update."""
        p = self.positions.get(symbol)
        if not p: return
        p.mark = px
        def hit(level): return (px <= level) if p.side > 0 else (px >= level)
        def hit_up(level): return (px >= level) if p.side > 0 else (px <= level)
        if hit(p.liq): return self.close(symbol, 1, px, ts, "liquidated")
        if hit(p.stop): return self.close(symbol, 1, px, ts, self._stop_reason(p))
        if p.mode == "trend": return             # trend positions: stop/trail only, trail moves on closed bars (trail_bar)
        if p.mode == "hf":                       # maker target needs a trade THROUGH the level; then time exit
            thr = self.hf_thr
            if p.tp1 is not None and ((px > p.tp1 * (1 + thr)) if p.side > 0 else (px < p.tp1 * (1 - thr))):
                return self.close(symbol, 1, p.tp1, ts, "target (maker)", maker=True)
            return self._hf_time(p, px, ts)
        if p.tp1 is not None and not p.tp1_done and hit_up(p.tp1):
            self._tp1(p, p.tp1 if self.x.get("tp1_maker") else px, ts)
            if symbol not in self.positions: return
        if p.tp1_done and p.tp2 and hit_up(p.tp2): return self.close(symbol, 1, px, ts, "TP2")
        p.best = max(p.best, px) if p.side > 0 else min(p.best, px)
        if p.tp1_done: self._trail(p)
        if (ts - p.opened) / 60 >= float(self.x["max_hold_min"]):
            return self.close(symbol, 1, px, ts, f"max hold {self.x['max_hold_min']}m")

    def _hf_time(self, p, px, ts):
        if p.hold_min and (ts - p.opened) / 60 >= float(p.hold_min):
            return self.close(p.symbol, 1, px, ts, f"time exit {p.hold_min:g}m")

    def _stop_reason(self, p):
        if p.mode == "trend": return "trailing stop" if (p.stop - p.stop0) * p.side > 0 else "stop loss"
        if not p.tp1_done: return "stop loss"
        return "trailing stop" if (p.stop - p.entry) * p.side > p.r * 0.05 else "breakeven stop"

    def _tp1(self, p, px, ts):
        frac = float(self.x["tp1_frac"])
        p.tp1_done = True
        self.close(p.symbol, frac, px, ts, f"TP1 {self.x['tp1_r']}R", maker=bool(self.x.get("tp1_maker")))
        if p.symbol in self.positions:
            be = p.entry * (1 + p.side * float(self.x["be_offset_pct"]) / 100)
            p.stop = self.round_px(p.symbol, max(p.stop, be) if p.side > 0 else min(p.stop, be))
            self.on_event({"type": "stop_move", "ts": ts, "symbol": p.symbol, "stop": p.stop, "why": "breakeven after TP1"})

    def _trail(self, p):
        t = p.best - p.side * p.trail_dist
        if (t - p.stop) * p.side > 0: p.stop = t

    def trail_bar(self, symbol, high, low, atr, mult, ts=None):
        """Chandelier trail for trend positions, called once per CLOSED bar: stop = best extreme since entry - mult*ATR (ratchet)."""
        p = self.positions.get(symbol)
        if not p or not atr: return None
        p.best = max(p.best, high) if p.side > 0 else min(p.best, low)
        t = self.round_px(symbol, p.best - p.side * float(mult) * atr, up=p.side < 0)
        if (t - p.stop) * p.side > 0:
            old = p.stop; p.stop = t
            self.on_event({"type": "stop_move", "ts": ts or time.time(), "symbol": symbol, "stop": t, "old": old, "why": "trail"})
            return t
        return None

    def set_stop(self, symbol, stop):
        p = self.positions[symbol]; p.stop = stop

"""Execution interface for BULK (bulk.trade) perps. ONLY the paper executor is wired in; live is disabled.

Paper fills (BULK Growth Mode tier 1): taker 3.5 bps, maker 0 bps. Taker slippage = sweep of the live BULK L2 book for the
order's notional (cached by the engine), never below `slippage_pct` floor. Maker fills (reduce-only ALO limit at TP1) fill at the
limit price with maker fee, no slippage.

How a future LIVE executor maps the bot's actions to BULK order types (POST /api/v1/order, unified transaction, Ed25519):
  entry          -> market `m` (or limit `l` tif=IOC with a price cap)               + on-fill `of` children:
  stop loss      -> stop `st` (reduce-only, triggers on BULK mark/fair price, market on trigger)
  TP1 50%        -> limit `l` tif=ALO reduce-only resting at TP1 (maker 0 bps)     [or take_profit `tp`]
  SL + TP pair   -> range `rng` (OCO) when one exit level is used
  runner trail   -> trailing stop `trl` (trail_bps, step_bps) after TP1 / stop moved to breakeven by cancel+replace `st`
  manual close   -> market `m` reduce-only
RULES for the day a live executor is added (needs the user's explicit confirmation in chat first):
  * signing only with bulk-keychain using an AGENT wallet key from an env secret (e.g. BULK_AGENT_KEY in /etc/memebot.env, 600);
    never in config.yaml, chat, logs or git. Agent wallets can trade for the account; never sign transfers/withdrawals.
  * config `mode` stays hard-locked to "paper" by config.load(); flipping it needs a code change + user confirmation.
"""
import time
from dataclasses import dataclass

@dataclass
class Fill:
    price: float      # average fill price
    qty: float        # base-asset quantity filled
    fee: float        # quote (USD) fee paid
    slippage: float   # quote cost of slippage vs reference price (already inside price)
    maker: bool = False

class Executor:
    paper = True
    name = "base"
    def market(self, symbol: str, side: str, qty: float, ref_price: float) -> Fill: raise NotImplementedError
    def limit_alo(self, symbol: str, side: str, qty: float, price: float) -> Fill: raise NotImplementedError

class PaperExecutor(Executor):
    name = "paper"
    def __init__(self, fees: dict):
        self.taker = float(fees.get("taker_fee_pct", 0.035)) / 100
        self.maker_fee = float(fees.get("maker_fee_pct", 0.0)) / 100
        self.slip = float(fees.get("slippage_pct", 0.01)) / 100
        self.slip_sym = {k: float(v) / 100 for k, v in (fees.get("slippage_pct_by_symbol") or {}).items()}
        self.books = {}            # symbol -> (bids, asks, ts) set by the live engine
        self.book_max_age = 30
    def slippage(self, symbol, side, notional, ref):
        floor = self.slip_sym.get(symbol, self.slip)
        b = self.books.get(symbol)
        if b and time.time() - b[2] < self.book_max_age:
            from .feeds import book_slippage
            s = book_slippage(b[1] if side == "buy" else b[0], notional, ref)
            if s is None: return max(floor, 0.005)      # book thinner than the order: assume 0.5%
            return max(floor, s)
        return floor
    def market(self, symbol, side, qty, ref_price):
        s = self.slippage(symbol, side, abs(qty * ref_price), ref_price)
        px = ref_price * (1 + s) if side == "buy" else ref_price * (1 - s)
        return Fill(px, qty, abs(qty * px) * self.taker, abs(qty * ref_price) * s)
    def limit_alo(self, symbol, side, qty, price):
        return Fill(price, qty, abs(qty * price) * self.maker_fee, 0.0, True)

class LiveExecutorDisabled(Executor):
    """Placeholder so nobody wires a real exchange by accident."""
    paper = False
    name = "bulk-live-disabled"
    def market(self, *a, **k): raise RuntimeError("Live trading is disabled. Paper only - a real BULK executor needs explicit user confirmation.")
    limit_alo = market

def make(cfg) -> Executor:
    if cfg.get("mode", "paper") != "paper": raise RuntimeError("mode must be paper")
    return PaperExecutor(cfg.get("fees") or {})

"""DexBlocker Futures Mini App API (PAPER only).
Every /api/* request needs header `X-Telegram-Init-Data: <Telegram.WebApp.initData>` (HMAC-checked, only ALLOWED_USER_ID, 24h max age).
DEV_NO_AUTH=1 skips the check ONLY for local screenshots with demo data (no futbot.db)."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter, collections, hashlib, hmac, json, math, re, threading, time, uuid
from urllib.parse import parse_qsl
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

def _allowed_user_id():
    raw = (os.environ.get("ALLOWED_USER_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip().strip('"').strip("'")
    if not raw:
        raise RuntimeError("Set TELEGRAM_CHAT_ID or ALLOWED_USER_ID (no hardcoded default)")
    return int(raw)

ALLOWED_USER_ID = _allowed_user_id()
MAX_AGE = 86400
SYMBOLS = ["BTC", "ETH", "SOL"]   # BULK -USD perps (BNB-USD is SUSPENDED on BULK)
app = FastAPI(title="DexBlocker Futures MiniApp API", docs_url=None, redoc_url=None)

def validate_init_data(init_data: str) -> dict:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip().strip("\"'")
    if not token: raise HTTPException(500, "server not configured")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True)); recv = pairs.pop("hash", None)
    if not recv: raise HTTPException(401, "missing hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), recv): raise HTTPException(401, "bad signature")
    if time.time() - int(pairs.get("auth_date", 0)) > MAX_AGE: raise HTTPException(401, "initData expired")
    user = json.loads(pairs.get("user", "{}"))
    if user.get("id") != ALLOWED_USER_ID: raise HTTPException(403, "forbidden")
    return user

def dev(): return os.environ.get("DEV_NO_AUTH") == "1" and not adapter.available()

@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/api/") and not dev():
        try: validate_init_data(request.headers.get("x-telegram-init-data", ""))
        except HTTPException as e: return JSONResponse({"error": e.detail}, status_code=e.status_code)
    return await call_next(request)

def real(): return adapter.available()

# ---------------- demo data (no bot DB yet / local screenshots) ----------------
D_PX = {"BTC": 78012.4, "ETH": 2291.35, "SOL": 94.47}
def _demo_curve():
    t0 = time.time() - 9 * 86400; e = 1000.0; out = []
    for i in range(9 * 24):
        e += math.sin(i / 7) * 1.6 + (0.35 if i % 5 else -1.1); out.append([t0 + i * 3600, round(e, 2)])
    return out
def demo_status():
    c = _demo_curve(); eq = c[-1][1]
    return {"demo": True, "mode": "PAPER", "unit": "USD", "live_control": True, "online": True, "heartbeat_age_s": 2, "running": True,
            "equity": eq, "balance": round(eq - 6.1, 2), "start_balance": 1000.0, "total_pnl": round(eq - 1000, 2), "total_pnl_pct": round((eq - 1000) / 10, 2),
            "today_pnl": 8.42, "today_pnl_pct": 0.83, "today_realized": 2.31, "today": {"trades": 5, "wins": 3, "losses": 2, "pnl": 2.31, "win_rate": 60.0, "profit_factor": 1.4, "avg_r": 0.12, "fees": 3.9},
            "all": {"trades": 61, "wins": 29, "losses": 32, "pnl": round(eq - 1000, 2), "win_rate": 47.5, "profit_factor": 1.12, "avg_r": 0.06, "fees": 48.2},
            "open_positions": 2, "max_open": 3, "used_margin": 812.4, "breaker": {"tripped": False, "reason": None, "consec_losses": 1},
            "risk": {"risk_pct": 1.0, "leverage": 3, "daily_loss_cap_pct": 3.0, "max_consecutive_losses": 4},
            "strategy": {"name": "hf_maker", "label": "HF maker scalper 1m · EXPERIMENT", "experimental": True, "paper": True, "tf": "1m", "trend_tf": None, "target_trades_per_day": 50, "risk_pct": 0.25, "summary": "maker quotes at EMA60 ∓ 3 ATR(1m) every minute, fill only on trade-through; exit maker at EMA, stop 8 ATR (taker), max 120 min · risk 0.25%/trade", "expect": {'pf': 0.76, 'win_pct': 65.3, 'trades_per_day': 60.3, 'trades_per_week': 422.1, 'avg_r': -0.067, 'max_dd_pct': 91.92, 'net_pct': -91.84, 'days': 245, 'bulk_60d': {'pf': 0.61, 'win_pct': 62.3, 'trades_per_day': 52.5, 'net_pct': -63.94, 'max_dd_pct': 64.23}}},
            "venue": "BULK", "source": "bulk", "ws": True, "sources": {"bulk": "ok 210ms", "hyperliquid": "ok 380ms", "okx": "ok 150ms"}, "proxy": None, "suspended": {"BNB": "SUSPENDED"},
            "prices": {s: {"last": p, "mark": p, "age": 0.4} for s, p in D_PX.items()}, "equity_curve": c,
            "daily_pnl": [["10-25", 6.2], ["10-26", -11.4], ["10-27", 9.8], ["10-28", 3.1], ["10-29", -4.2], ["10-30", 12.6], ["10-31", -2.3], ["11-01", 7.9], ["11-02", 2.3]],
            "symbols": SYMBOLS, "enabled_symbols": SYMBOLS, "updated": int(time.time())}
DEMO_POS = [
    {"symbol": "BTC", "side": "LONG", "lev": 3.0, "entry": 77961.3, "mark": 78012.4, "upnl": 0.25, "upnl_pct": 0.19, "liq": 52624.0, "sl": 77410.2, "sl0": 77410.2,
     "tp1": 78240.0, "tp1_done": False, "qty": 0.00482, "qty0": 0.00482, "left_pct": 100, "notional": 376.0, "margin": 125.3, "held_min": 12, "manual": False, "setup": "hf_maker", "funding": 0, "fees": 0.0, "mode": "hf"},
    {"symbol": "ETH", "side": "SHORT", "lev": 3.0, "entry": 2298.4, "mark": 2291.35, "upnl": 2.05, "upnl_pct": 1.23, "liq": 3053.5, "sl": 2307.6, "sl0": 2307.6,
     "tp1": 2289.2, "tp1_done": False, "qty": 0.2907, "qty0": 0.2907, "left_pct": 100, "notional": 666.1, "margin": 222.0, "held_min": 7, "manual": True, "setup": "manual", "funding": -0.01, "fees": 0.23}]
def demo_hist():
    rows = [("SOL", 1, 94.12, 94.61, 4.9, 1.21, "trailing stop", "pullback", 38), ("ETH", -1, 2308.2, 2312.9, -5.6, -1.06, "stop loss", "pullback", 12),
            ("BTC", -1, 78410.0, 78180.2, 3.8, 0.71, "max hold 60m", "pullback", 60), ("ETH", 1, 2281.5, 2281.9, 1.1, 0.42, "breakeven stop", "pullback", 26),
            ("SOL", -1, 95.31, 95.62, -5.4, -1.05, "stop loss", "breakout", 9), ("BTC", 1, 77650.0, 77921.0, 6.7, 1.38, "trailing stop", "breakout", 44)]
    now = time.time(); out = []
    for i, (s, sd, e, x, pnl, r, why, st, held) in enumerate(rows):
        cl = now - (i + 1) * 5400
        out.append({"symbol": s, "side": "LONG" if sd > 0 else "SHORT", "entry": e, "exit": x, "pnl": pnl, "r_mult": r, "reason": why, "setup": st, "lev": 3.0,
                    "notional": 1500, "held_min": held, "closed_iso": adapter._iso(cl), "opened_iso": adapter._iso(cl - held * 60), "fees": 1.5, "funding": -0.02,
                    "manual": i == 3, "tp1": r > 0.3, "pnl_pct_margin": round(pnl / 500 * 100, 2)})
    return out
def demo_signals():
    Q = [("BTC", "in position (no new quotes)", "position open · target 78,240.0 · stop 77,410.2", None, None, 77890.4, 0.071),
         ("ETH", "quotes resting (maker)", "quoting: BUY 2,282.64 (0.38% below) / SELL 2,300.06 (0.38% above) · EMA60 2,291.35 · ATR 0.095%", 2282.64, 2300.06, 2291.35, 0.095),
         ("SOL", "quotes resting (maker)", "quoting: BUY 94.11 (0.38% below) / SELL 94.83 (0.38% above) · EMA60 94.47 · ATR 0.190%", 94.11, 94.83, 94.47, 0.19)]
    st = {"name": "hf_maker", "label": "HF maker scalper 1m · EXPERIMENT", "experimental": True, "paper": True, "tf": "1m", "trend_tf": None, "target_trades_per_day": 50, "risk_pct": 0.25, "summary": "maker quotes at EMA60 ∓ 3 ATR(1m) every minute, fill only on trade-through; exit maker at EMA, stop 8 ATR (taker), max 120 min · risk 0.25%/trade", "expect": {'pf': 0.76, 'win_pct': 65.3, 'trades_per_day': 60.3, 'trades_per_week': 422.1, 'avg_r': -0.067, 'max_dd_pct': 91.92, 'net_pct': -91.84, 'days': 245, 'bulk_60d': {'pf': 0.61, 'win_pct': 62.3, 'trades_per_day': 52.5, 'net_pct': -63.94, 'max_dd_pct': 64.23}}}
    return {"demo": True, "entry_tf": "1m", "strategy": st, "signals": [{"symbol": s, "enabled": True, "price": D_PX[s], "bid": b, "ask": a, "mean": m, "atr_pct": at,
            "side": 0, "setup": "hf_maker", "reason": why, "action": act, "checked_ago_s": 21, "funding_pct": [0.00125, 0.0009, -0.0004][i], "funding_hours": 1, "status": "TRADING"}
            for i, (s, act, why, b, a, m, at) in enumerate(Q)],
            "log": [{"time": adapter._iso(time.time() - 700), "symbol": "BTC", "side": 1, "setup": "hf_maker", "reason": "maker BUY filled @ 77,961.3 (EMA60 - 3 ATR) · target 78,240.0", "action": "filled (maker)"},
                    {"time": adapter._iso(time.time() - 2900), "symbol": "SOL", "side": -1, "setup": "hf_maker", "reason": "maker SELL filled @ 95.02 (EMA60 + 3 ATR) · target 94.61", "action": "filled (maker)"}]}
DEMO_SET = {"risk.risk_pct": 1.0, "risk.leverage": 3.0, "risk.max_open_positions": 2, "risk.daily_loss_cap_pct": 3.0, "risk.max_consecutive_losses": 4,
            "exits.tp1_r": 1.0, "exits.trail_atr_mult": 1.5, "exits.max_hold_min": 60, "strategy.adx_min": 25, "report.every_days": 2, "enabled_symbols": SYMBOLS}

# ---------------- read endpoints ----------------
@app.get("/api/status")
def status(): return adapter.status() if real() else demo_status()
@app.get("/api/positions")
def positions(): return adapter.positions() if real() else {"demo": True, "open": DEMO_POS, "live": True}
@app.get("/api/history")
def history(): return adapter.history() if real() else {"demo": True, "closed": demo_hist()}
@app.get("/api/signals")
def signals(): return adapter.signals() if real() else demo_signals()
def demo_candles(sym, tf):
    sec = adapter.CHART_TF[tf]; n = 240; now = int(time.time() // sec * sec); rnd = __import__("random").Random(hash((sym, tf)) & 0xffff)
    vol = D_PX[sym] * 0.0009 * math.sqrt(sec / 60); p = D_PX[sym]; out = []
    for i in range(n):                                       # walk BACKWARDS from the demo price so the last close == D_PX
        c = p; o = c - rnd.gauss(0, vol) - math.sin(i / 17) * vol * 0.4
        out.append({"time": now - i * sec, "open": round(o, 4), "high": round(max(o, c) + abs(rnd.gauss(0, vol * 0.6)), 4),
                    "low": round(min(o, c) - abs(rnd.gauss(0, vol * 0.6)), 4), "close": round(c, 4)}); p = o
    out.reverse(); t = [x["time"] for x in out]
    def T(k, sd, pnl, held): return {"symbol": sym, "side": sd, "opened": t[k], "closed": t[min(n - 1, k + held)], "pnl": pnl, "manual": 0}
    trades = [T(40, 1, 1.42, 9), T(70, -1, -2.10, 14), T(110, 1, 0.88, 6), T(150, -1, 1.05, 11), T(190, 1, -1.73, 8)]
    pos = [{"symbol": sym, "side": 1, "opened": t[-12], "entry": out[-12]["close"], "stop": round(out[-12]["close"] * 0.993, 4),
            "tp1": round(out[-12]["close"] * 1.0035, 4), "tp1_done": False}] if sym == "BTC" else []
    quotes = {sym: {"bid": round(D_PX[sym] * 0.9962, 4), "ask": round(D_PX[sym] * 1.0038, 4)}} if sym != "BTC" else {}
    mk, lines = adapter.chart_markers(trades, pos, quotes, sym, t[0], sec)
    return {"demo": True, "symbol": sym, "tf": tf, "source": "demo data", "candles": out, "markers": mk, "lines": lines, "mark": D_PX[sym], "updated": int(time.time())}
@app.get("/api/candles")
def candles(symbol: str = "BTC", tf: str = "1m"):
    sym = symbol.upper().replace("-USD", "")
    if sym not in _syms(): raise HTTPException(422, "unknown symbol")
    if tf not in adapter.CHART_TF: raise HTTPException(422, "tf must be 1m, 5m or 15m")
    if not real(): return demo_candles(sym, tf)
    try: return adapter.candles(sym, tf)
    except Exception as e: raise HTTPException(503, f"BULK candles unavailable: {str(e)[:80]}")

@app.get("/api/settings")
def get_settings():
    if real(): return adapter.get_settings()
    from miniapp.settings_spec import FIELDS
    return {"demo": True, "editable": False, "mode": "PAPER", "running": True,
            "fields": [{**f, "value": DEMO_SET[f["key"]], **({"options": SYMBOLS} if f["type"] == "symbols" else {})} for f in FIELDS]}

class SettingsIn(BaseModel):
    changes: dict
@app.post("/api/settings")
def post_settings(body: SettingsIn):
    if any(k.split(".")[0] in ("mode", "paper", "fees", "data") or "key" in k.lower() for k in body.changes): raise HTTPException(400, "forbidden key")
    if not real(): raise HTTPException(409, "demo mode: settings are read-only")
    try: return {"ok": True, "applied": adapter.save_settings(body.changes)}
    except ValueError as e: raise HTTPException(422, str(e))
    except RuntimeError as e: raise HTTPException(409, str(e))

class Toggle(BaseModel):
    running: bool
@app.post("/api/toggle")
def toggle(t: Toggle):
    if not real(): return {"ok": True, "mock": True, "running": t.running, "mode": "PAPER"}
    try: adapter.set_running(t.running)
    except RuntimeError as e: raise HTTPException(409, str(e))
    return {"ok": True, "pending": True, "running": t.running, "mode": "PAPER"}

# ---------------- manual PAPER trades ----------------
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
RATE = {"preview": (30, 60), "trade": (10, 60)}
_hits, _rl = collections.defaultdict(collections.deque), threading.Lock()
def manual_user(request: Request) -> dict:
    if dev(): return {"id": 0, "dev": True}
    return validate_init_data(request.headers.get("x-telegram-init-data", ""))
def rate_limit(user, bucket):
    n, win = RATE[bucket]; now = time.time(); q = _hits[(user.get("id"), bucket)]
    with _rl:
        while q and now - q[0] > win: q.popleft()
        if len(q) >= n: raise HTTPException(429, f"too many requests - wait {int(win - (now - q[0])) + 1}s")
        q.append(now)
def _syms(): return (adapter._cfg().get("symbols") if real() else None) or SYMBOLS
def _sym(s):
    s = (s or "").upper().replace("USDT", "").replace("-USD", "")
    if s not in _syms(): raise HTTPException(422, "symbol must be one of " + ", ".join(_syms()))
    return s
def _side(x):
    x = str(x).lower()
    if x in ("long", "1", "buy"): return 1
    if x in ("short", "-1", "sell"): return -1
    raise HTTPException(422, "side must be long or short")
def _key(k, hdr):
    k = k or hdr
    if not k or not KEY_RE.match(k): raise HTTPException(422, "idempotency_key required (8-64 chars [A-Za-z0-9_-])")
    return k
def _call(kind, payload, rid, timeout=20.0):
    try: return adapter.manual_call(kind, payload, rid, timeout)
    except adapter.ManualFail as e: raise HTTPException(e.code, e.msg)

class PreviewIn(BaseModel):
    symbol: str
    side: str
    risk_pct: Optional[float] = None
class OpenIn(PreviewIn):
    idempotency_key: Optional[str] = None
class CloseIn(BaseModel):
    symbol: Optional[str] = None
    pct: float = 100
    all: bool = False
    idempotency_key: Optional[str] = None

def _demo_preview(sym, side, risk):
    px = D_PX[sym]; sd = px * 0.0125; risk = risk or 1.0; qty = min(10.0 * risk / (sd + 0.0007 * px), 1500 / px)
    return {"demo": True, "symbol": sym, "side": side, "price": px, "mark": px, "stop": px - side * sd, "tp1": None, "mode": "trend", "qty": qty, "notional": qty * px,
            "margin": qty * px / 3, "lev": 3.0, "liq": px * (1 - side * (1 / 3 - 0.005)), "risk_usd": qty * (sd + 0.0007 * px), "risk_pct": risk,
            "capped_by_leverage": qty * px >= 1499, "equity": 1006.2, "allowed": sym not in ("BTC", "ETH"), "why": "already in a position on this symbol" if sym in ("BTC", "ETH") else "ok",
            "est_fees": qty * px * 0.0007, "est_slippage_pct": 0.01, "min_notional": 1.0 if sym == "BTC" else 50.0, "signal": demo_signals()["signals"][SYMBOLS.index(sym)]["reason"]}

@app.post("/api/manual/preview")
def manual_preview(body: PreviewIn, user: dict = Depends(manual_user)):
    sym, side = _sym(body.symbol), _side(body.side); rate_limit(user, "preview")
    if body.risk_pct is not None and not 0.1 <= body.risk_pct <= 2: raise HTTPException(422, "risk must be 0.1-2%")
    if not real(): return _demo_preview(sym, side, body.risk_pct)
    return _call("preview", {"symbol": sym, "side": side, "risk_pct": body.risk_pct}, "pv-" + uuid.uuid4().hex, 25.0)

@app.post("/api/manual/open")
def manual_open(body: OpenIn, user: dict = Depends(manual_user), idempotency_key: Optional[str] = Header(None)):
    sym, side, rid = _sym(body.symbol), _side(body.side), _key(body.idempotency_key, idempotency_key)
    if body.risk_pct is not None and not 0.1 <= body.risk_pct <= 2: raise HTTPException(422, "risk must be 0.1-2%")
    rate_limit(user, "trade")
    if not real(): raise HTTPException(409, "demo mode: bot not connected, nothing executed")
    return _call("open", {"symbol": sym, "side": side, "risk_pct": body.risk_pct}, "open-" + rid)

@app.post("/api/manual/close")
def manual_close(body: CloseIn, user: dict = Depends(manual_user), idempotency_key: Optional[str] = Header(None)):
    rid = _key(body.idempotency_key, idempotency_key)
    sym = None if body.all else _sym(body.symbol)
    if not body.all and not 1 <= body.pct <= 100: raise HTTPException(422, "pct must be 1-100")
    rate_limit(user, "trade")
    if not real(): raise HTTPException(409, "demo mode: bot not connected, nothing executed")
    if body.all: return _call("close_all", {}, "close-" + rid, 45.0)
    return _call("close", {"symbol": sym, "pct": body.pct}, "close-" + rid)

@app.get("/healthz")
def healthz(): return {"ok": True}

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
@app.get("/")
def index(): return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

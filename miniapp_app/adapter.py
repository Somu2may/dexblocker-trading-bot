"""Real-data adapter for the futures paper bot. Reads futbot.db (read-only) + live snapshot in miniapp.db; writes only
miniapp.db control/manual_req and whitelisted config keys. Paths: BOT_DIR, DATA_DIR (default BOT_DIR/data), MEMEBOT_CONFIG."""
import json, os, sqlite3, time, datetime as dt, tempfile
import yaml
from miniapp import hooks
from miniapp.settings_spec import FIELDS, validate, get, put
BOT = os.environ.get("BOT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot"))
DATA = os.environ.get("DATA_DIR", os.path.join(BOT, "data"))
CONFIG = os.environ.get("MEMEBOT_CONFIG", os.path.join(BOT, "config.yaml"))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HOOK_FRESH = 30

def available(): return os.path.exists(os.path.join(DATA, "futbot.db"))
def _db(): return sqlite3.connect(f"file:{os.path.join(DATA, 'futbot.db')}?mode=ro", uri=True, timeout=3)
def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items(): out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out
def _cfg():
    """Live config over the repo default (new keys like report.every_days show up before the bot first saves its config)."""
    try: base = yaml.safe_load(open(os.path.join(BOT, "config.yaml"))) or {}
    except Exception: base = {}
    try: live = yaml.safe_load(open(CONFIG)) or {}
    except Exception: live = {}
    c = _merge(base, live) if os.path.abspath(CONFIG) != os.path.abspath(os.path.join(BOT, "config.yaml")) else (live or base)
    if int(live.get("defaults_rev") or 1) < int(base.get("defaults_rev") or 1) and base.get("strategy"):
        c["strategy"]["name"] = base["strategy"]["name"]          # same one-time default push as futbot.config.load
    return c
def _iso(ts): return dt.datetime.fromtimestamp(ts, IST).isoformat(timespec="seconds")
def _midnight(): return dt.datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
def _state(c, k, d=None):
    r = c.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone(); return json.loads(r[0]) if r else d
def _rows(c, sql, *a):
    cur = c.execute(sql, a); cols = [x[0] for x in cur.description]; return [dict(zip(cols, r)) for r in cur.fetchall()]

def hook_status():
    try:
        m = hooks.connect(DATA); r = m.execute("SELECT v FROM status WHERE k='status'").fetchone(); m.close()
    except Exception: return None
    if not r: return None
    s = json.loads(r[0]); return s if time.time() - s["heartbeat"] < HOOK_FRESH else None
def hooks_live(): return hook_status() is not None
def _hb_age():
    try: return time.time() - float(open(os.path.join(DATA, "heartbeat")).read().strip())
    except Exception: return None

def _trade_out(t):
    return {**t, "side": "LONG" if t["side"] > 0 else "SHORT", "opened_iso": _iso(t["opened"]), "closed_iso": _iso(t["closed"]),
            "held_min": round((t["closed"] - t["opened"]) / 60, 1), "pnl_pct_margin": round(t["pnl"] / (t["notional"] / t["lev"]) * 100, 2) if t["notional"] else 0}

def summary(T):
    w = [t["pnl"] for t in T if t["pnl"] > 0]; l = [t["pnl"] for t in T if t["pnl"] <= 0]
    return {"trades": len(T), "wins": len(w), "losses": len(l), "pnl": round(sum(w) + sum(l), 2), "win_rate": round(len(w) / len(T) * 100, 1) if T else 0,
            "profit_factor": round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None, "avg_r": round(sum(t["r_mult"] or 0 for t in T) / len(T), 2) if T else 0,
            "fees": round(sum(t["fees"] or 0 for t in T), 2)}

def status():
    cfg = _cfg(); start = float((cfg.get("paper") or {}).get("start_balance", 1000)); h = hook_status(); c = _db()
    meme = _is_meme(cfg, h)
    T = _rows(c, "SELECT * FROM trades ORDER BY closed"); eqrows = c.execute("SELECT ts, equity FROM equity ORDER BY ts").fetchall()
    if h:
        bal, positions, br = h["balance"], h["positions"], {"tripped": h.get("breaker"), "consec": h.get("consec_losses"), "day_start_eq": h.get("day_start_eq")}
    elif meme:
        bal = _state(c, "meme_balance", start); br = _state(c, "meme_breakers", {}) or {}
        positions = list((_state(c, "meme_positions", {}) or {}).values())
    else:
        bal = _state(c, "balance", start); br = _state(c, "breakers", {}) or {}
        positions = list((_state(c, "positions", {}) or {}).values())
    if h:
        eq = h["equity"]
    elif meme:
        # cash was spent to buy the tokens; equity is cash plus mark value, not futures-style uPnL
        eq = bal + sum((p.get("mark") or p["entry"]) * p.get("qty", 0) for p in positions)
    else:
        eq = bal + sum((p.get("mark", p["entry"]) - p["entry"]) * p.get("qty", 0) * p.get("side", 1) for p in positions)
    running = h["running"] if h else _state(c, "running", True); c.close()
    mid = _midnight(); today = [t for t in T if t["closed"] >= mid]
    day_start = (h or {}).get("day_start_eq") or br.get("day_start_eq") or start
    curve = [[ts, round(e, 2)] for ts, e in eqrows][-500:] + [[time.time(), round(eq, 2)]]
    if len(curve) < 2: curve = [[T[0]["opened"] if T else time.time() - 3600, start]] + curve
    daily = {}
    for t in T: d = dt.datetime.fromtimestamp(t["closed"], IST).strftime("%m-%d"); daily[d] = round(daily.get(d, 0) + t["pnl"], 2)
    age = _hb_age(); online = age is not None and age < 300
    breaker = (h or {}).get("breaker") or br.get("tripped"); stale = (h or {}).get("stale")
    if not online: breaker = "bot offline (no heartbeat)"
    return {"demo": False, "mode": "PAPER", "unit": "USD", "live_control": bool(h), "online": online, "heartbeat_age_s": round(age) if age is not None else None,
            "running": bool(running) and online, "equity": round(eq, 2), "balance": round(bal, 2), "start_balance": start,
            "total_pnl": round(eq - start, 2), "total_pnl_pct": round((eq - start) / start * 100, 2),
            "today_pnl": round(eq - day_start, 2), "today_pnl_pct": round((eq - day_start) / day_start * 100, 2) if day_start else 0,
            "today_realized": round(sum(t["pnl"] for t in today), 2), "today": summary(today), "all": summary(T),
            "open_positions": len(positions), "max_open": _max_open(cfg, h),
            "used_margin": round((h or {}).get("used_margin") or 0, 2),
            "breaker": {"tripped": bool(breaker or stale), "reason": breaker or stale, "consec_losses": (h or {}).get("consec_losses", br.get("consec", 0))},
            "risk": {k: (cfg.get("risk") or {}).get(k) for k in ("risk_pct", "leverage", "daily_loss_cap_pct", "max_consecutive_losses")},
            "venue": (h or {}).get("venue") or ("SOL" if _is_meme(cfg, h) else "BULK"), "source": (h or {}).get("source"), "ws": (h or {}).get("ws"), "sources": (h or {}).get("sources") or {},
            "strategy": (h or {}).get("strategy") or _strategy_cfg(cfg), "history_source": (h or {}).get("history_source") or {},
            "proxy": (h or {}).get("proxy"), "suspended": (h or {}).get("suspended") or {}, "rules": (h or {}).get("rules") or {},
            "prices": (h or {}).get("prices") or {}, "equity_curve": curve, "daily_pnl": [[k, v] for k, v in sorted(daily.items())][-14:],
            "symbols": cfg.get("symbols") or [], "enabled_symbols": cfg.get("enabled_symbols") or [], "updated": int(time.time())}

def _is_meme(cfg, h=None):
    return (((h or {}).get("strategy") or {}).get("name") or ((cfg.get("strategy") or {}).get("name"))) == "gmgn_meme"
def _max_open(cfg, h=None):
    if _is_meme(cfg, h):
        return int((((cfg.get("strategy") or {}).get("gmgn") or {}).get("max_open_positions") or 4))
    hf = (cfg.get("strategy") or {}).get("name") == "hf_maker"
    if hf:
        return int(((((cfg.get("strategy") or {}).get("hf") or {}).get("risk") or {}).get("max_open_positions") or 3))
    return int((cfg.get("risk") or {}).get("max_open_positions", 2))
DC_BLOCK = {"donchian_1h": ("donchian", "1h", "4h"), "donchian_15m": ("donchian_15m", "15m", "1h")}
def _strategy_cfg(cfg):
    s = cfg.get("strategy") or {}; n = s.get("name") or "gmgn_meme"
    if n == "gmgn_meme":
        g = s.get("gmgn") or {}
        return {"name": n, "experimental": False, "paper": True, "label": "Solana memecoin scanner · PAPER", "tf": "scan", "trend_tf": None,
                "risk_pct": g.get("position_size_pct"), "stop_loss_pct": g.get("stop_loss_pct"),
                "daily_loss_cap_pct": g.get("daily_loss_cap_pct"), "max_open": g.get("max_open_positions"),
                "summary": "Fail-closed gates. A price quote is not a sell proof."}
    if n == "hf_maker":
        h = s.get("hf") or {}; rk = h.get("risk") or {}
        return {"name": n, "experimental": True, "paper": True, "label": "HF maker scalper 1m · EXPERIMENT", "tf": "1m", "trend_tf": None,
                "target_trades_per_day": h.get("target_trades_per_day", 50), "risk_pct": rk.get("risk_pct"), "expect": h.get("expect") or {},
                "summary": f"maker quotes at EMA{h.get('ema', 60)} ∓ {h.get('band_atr', 4)} ATR(1m), exit at EMA (maker), stop {h.get('stop_atr', 8)} ATR, max {h.get('max_hold_min', 120)} min"}
    if n in DC_BLOCK:
        blk, tf, ttf = DC_BLOCK[n]; d = s.get(blk) or {}; tf, ttf = d.get("tf", tf), d.get("trend_tf", ttf)
        return {"name": n, "experimental": True, "paper": True, "label": f"Donchian-{d.get('channel', 20)} {tf} breakout · EXPERIMENTAL",
                "tf": tf, "trend_tf": ttf, "channel": d.get("channel", 20), "long_only": bool(d.get("long_only", True))}
    return {"name": n, "experimental": False, "paper": True, "label": "5m pullback scalper (backtest: no edge)", "tf": s.get("entry_tf", "5m"), "trend_tf": s.get("trend_tf", "15m")}

def positions():
    h = hook_status(); c = _db(); cfg = _cfg(); meme = _is_meme(cfg, h)
    if h:
        ps = h["positions"]
    elif meme:
        ps = list((_state(c, "meme_positions", {}) or {}).values())
    else:
        ps = [{**p, "upnl": 0, "upnl_pct_margin": 0} for p in (_state(c, "positions", {}) or {}).values()]
    c.close(); out = []
    for p in ps:
        qty0 = p.get("qty0") or p.get("qty") or 1
        out.append({"symbol": p["symbol"], "side": "LONG" if p.get("side", 1) > 0 else "SHORT", "lev": p.get("lev") or 1, "entry": p["entry"], "mark": p.get("mark") or p["entry"],
                    "upnl": round(p.get("upnl") or 0, 2), "upnl_pct": round(p.get("upnl_pct_margin") or 0, 2), "liq": p.get("liq"), "sl": p.get("stop"), "sl0": p.get("stop0") or p.get("stop"),
                    "tp1": p.get("tp1"), "tp1_done": p.get("tp1_done"), "qty": p.get("qty"), "qty0": qty0, "left_pct": round((p.get("qty") or 0) / qty0 * 100) if qty0 else 0,
                    "notional": round((p.get("qty") or 0) * (p.get("mark") or p["entry"]), 2), "margin": round((p.get("margin") or 0) * (p.get("qty") or 0) / qty0, 2) if qty0 else 0,
                    "opened": _iso(p["opened"]), "held_min": round((time.time() - p["opened"]) / 60), "manual": bool(p.get("manual")), "setup": p.get("setup"),
                    "funding": round(p.get("funding") or 0, 4), "fees": round(p.get("fees") or 0, 3), "mode": p.get("mode") or "scalp",
                    "mint": p.get("mint"), "mcap": p.get("mcap"), "liquidity": p.get("liquidity"), "tp_done": p.get("tp_done") or []})
    return {"demo": False, "open": out, "live": bool(h), "meme": meme}

def history(limit=100):
    c = _db(); T = _rows(c, "SELECT * FROM trades ORDER BY closed DESC LIMIT ?", limit)
    rej = _rows(c, "SELECT * FROM signal_log WHERE action LIKE 'reject%' OR setup='gmgn_meme' ORDER BY ts DESC LIMIT 40")
    c.close()
    return {"demo": False, "closed": [_trade_out(t) for t in T],
            "rejected": [{**x, "time": _iso(x["ts"])} for x in rej if str(x.get("action") or "").startswith("reject")]}

def signals():
    h = hook_status(); c = _db(); cfg = _cfg()
    if _is_meme(cfg, h):
        rows = (h or {}).get("scanner")
        if rows is None:
            rows = []
            for r in c.execute("SELECT symbol, data FROM signals"):
                try: rows.append(json.loads(r[1]))
                except Exception: pass
        log = _rows(c, "SELECT * FROM signal_log ORDER BY ts DESC LIMIT 30"); c.close()
        st = (h or {}).get("strategy") or _strategy_cfg(cfg)
        return {"demo": False, "meme": True, "signals": rows, "scanner": rows,
                "log": [{**x, "time": _iso(x["ts"])} for x in log], "entry_tf": "scan", "strategy": st}
    sig = (h or {}).get("signals") or {r[0]: json.loads(r[2]) for r in c.execute("SELECT * FROM signals")}
    log = _rows(c, "SELECT * FROM signal_log ORDER BY ts DESC LIMIT 30"); c.close()
    cfg = _cfg(); prices = (h or {}).get("prices") or {}; fund = (h or {}).get("funding") or {}
    out = []
    for s in cfg.get("symbols") or []:
        d = sig.get(s) or {}; dg = d.get("diag") or {}
        susp = ((h or {}).get("suspended") or {}).get(s)
        if susp: d = {**d, "side": 0, "reason": f"BULK {s}-USD is {susp} - skipped", "action": None}
        out.append({"symbol": s, "enabled": s in (cfg.get("enabled_symbols") or []), "status": susp or "TRADING", "price": (prices.get(s) or {}).get("last") or d.get("price"),
                    "trend": dg.get("trend"), "dch_high": dg.get("dch_high"), "dist_pct": dg.get("dist_pct"), "ema4h": dg.get("ema_trend", dg.get("ema4h")), "close4h": dg.get("close_trend", dg.get("close4h")),
                    "dch_low": dg.get("dch_low"), "dist_low_pct": dg.get("dist_low_pct"), "bid": dg.get("bid"), "ask": dg.get("ask"), "mean": dg.get("mean"),
                    "channel": dg.get("channel"), "trail_stop": dg.get("trail_stop"), "history": dg.get("history"), "adx": dg.get("adx"), "rsi": dg.get("rsi"), "atr_pct": dg.get("atr_pct"), "vol_x": dg.get("vol_x"),
                    "side": d.get("side", 0), "setup": d.get("setup"), "reason": d.get("reason") or "waiting for first closed bar", "action": d.get("action"),
                    "checked_ago_s": int(time.time() - d["checked"]) if d.get("checked") else None,
                    "funding_pct": round(fund[s]["rate"] * 100, 4) if s in fund else None, "funding_hours": (fund.get(s) or {}).get("hours")})
    st = (h or {}).get("strategy") or _strategy_cfg(cfg)
    return {"demo": False, "signals": out, "log": [{**x, "time": _iso(x["ts"])} for x in log], "entry_tf": st.get("tf"), "strategy": st}

def get_settings():
    cfg = _cfg(); h = hook_status()
    return {"demo": False, "editable": h is not None, "mode": "PAPER", "running": (h or {}).get("running"),
            "fields": [{**f, "value": get(cfg, f["key"]), **({"options": cfg.get("symbols")} if f["type"] == "symbols" else {})} for f in FIELDS if get(cfg, f["key"]) is not None]}

def save_settings(changes):
    if not hooks_live(): raise RuntimeError("bot is offline; settings are read-only right now")
    cfg = _cfg(); clean = {k: validate(k, v, cfg.get("symbols")) for k, v in changes.items()}
    for k, v in clean.items():
        if get(cfg, k) is None: raise ValueError(f"{k} missing in config")
        put(cfg, k, v)
    cfg["mode"] = "paper"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(CONFIG)))
    with os.fdopen(fd, "w") as f: yaml.safe_dump(cfg, f, sort_keys=False)
    os.replace(tmp, CONFIG)
    m = hooks.connect(DATA); m.execute("REPLACE INTO control VALUES('reload',?)", (str(time.time()),)); m.commit(); m.close()
    return clean

def set_running(on):
    if not hooks_live(): raise RuntimeError("bot is offline; use /pause or /resume in Telegram")
    m = hooks.connect(DATA); m.execute("REPLACE INTO control VALUES('running',?)", ("1" if on else "0",)); m.commit(); m.close()

class ManualFail(Exception):
    def __init__(self, msg, code): super().__init__(msg); self.msg, self.code = msg, code

def manual_call(kind, payload, rid, timeout=20.0):
    """Queue a request (id = idempotency key) and wait for the bot. Same id again returns the FIRST result (never runs twice)."""
    if not hooks_live(): raise ManualFail("bot is offline - try again in a minute", 409)
    m = hooks.connect(DATA); body = json.dumps(payload, sort_keys=True)
    try:
        row = m.execute("SELECT kind, payload FROM manual_req WHERE id=?", (rid,)).fetchone()
        if row and (row[0] != kind or row[1] != body): raise ManualFail("idempotency key already used for a different request", 409)
        if not row: m.execute("INSERT OR IGNORE INTO manual_req(id, ts, kind, payload, status) VALUES(?,?,?,?,'pending')", (rid, time.time(), kind, body)); m.commit()
        end = time.time() + timeout
        while time.time() < end:
            st, res = m.execute("SELECT status, result FROM manual_req WHERE id=?", (rid,)).fetchone()
            if st in ("done", "error"):
                r = json.loads(res or "{}")
                if st == "error": raise ManualFail(r.get("error", "failed"), int(r.get("code", 400)))
                return r
            time.sleep(0.15)
        raise ManualFail("still processing - check Positions in a few seconds (it will not run twice)", 504)
    finally: m.close()

# ---------------- live chart: BULK klines + the bot's live mark + bot markers ----------------
CHART_TF = {"1m": 60, "5m": 300, "15m": 900}
BULK_KLINES = "https://mainnet-api1.bulk.trade/api/v1/klines"
_kc = {}
def _bulk_klines(sym, tf, n=240):
    """Public BULK REST klines (read-only, no keys), cached 5 s per symbol/timeframe."""
    key = (sym, tf); hit = _kc.get(key)
    if hit and time.time() - hit[0] < 5: return hit[1]
    import httpx
    sec = CHART_TF[tf]; now = int(time.time())
    r = httpx.get(BULK_KLINES, params={"symbol": f"{sym}-USD", "interval": tf, "startTime": (now - sec * (n + 1)) * 1000, "endTime": now * 1000},
                  headers={"User-Agent": "dexblocker-miniapp/1.0"}, timeout=6)
    r.raise_for_status()
    bars = sorted({x["t"] // 1000: (x["t"] // 1000, float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"])) for x in r.json()}.values())[-n:]
    _kc[key] = (time.time(), bars); return bars

def chart_markers(trades, positions, quotes, sym, t0, sec):
    """Entry/exit markers for closed bot trades + price lines for open positions (entry/stop/target) and resting maker quotes."""
    mk, lines = [], []
    b = lambda ts: int(ts // sec * sec)
    for t in trades:
        if t["symbol"] != sym or t["closed"] < t0: continue
        L = t["side"] > 0
        if t["opened"] >= t0:
            mk.append({"time": b(t["opened"]), "position": "belowBar" if L else "aboveBar", "shape": "arrowUp" if L else "arrowDown",
                       "color": "#00ff9d" if L else "#ff3b5c", "text": ("L" if L else "S") + (" M" if t.get("manual") else "")})
        mk.append({"time": b(t["closed"]), "position": "aboveBar" if L else "belowBar", "shape": "circle",
                   "color": "#00ff9d" if t["pnl"] > 0 else "#ff3b5c", "text": f"{t['pnl']:+.2f}"})
    for p in positions:
        if p["symbol"] != sym: continue
        L = p["side"] > 0
        if p["opened"] >= t0: mk.append({"time": b(p["opened"]), "position": "belowBar" if L else "aboveBar", "shape": "arrowUp" if L else "arrowDown",
                                         "color": "#00ff9d" if L else "#ff3b5c", "text": ("LONG" if L else "SHORT") + " open"})
        lines.append({"price": p["entry"], "color": "#9aa4b2", "title": "entry", "style": 0})
        lines.append({"price": p["stop"], "color": "#ff3b5c", "title": "stop", "style": 2})
        if p.get("tp1") is not None and not p.get("tp1_done"): lines.append({"price": p["tp1"], "color": "#00ff9d", "title": "target", "style": 2})
    q = (quotes or {}).get(sym) or {}
    if q.get("bid"): lines.append({"price": q["bid"], "color": "#3fa9ff", "title": "maker bid", "style": 1})
    if q.get("ask"): lines.append({"price": q["ask"], "color": "#ffb020", "title": "maker ask", "style": 1})
    mk.sort(key=lambda x: x["time"]); return mk, lines

def candles(sym, tf):
    if tf not in CHART_TF: raise ValueError("tf must be 1m, 5m or 15m")
    sec = CHART_TF[tf]; bars = _bulk_klines(sym, tf); h = hook_status() or {}
    live = ((h.get("prices") or {}).get(sym) or {})
    mark = live.get("mark") or live.get("last")
    now = int(time.time() // sec * sec)
    if bars and tf != "1m" and bars[-1][0] < now:            # forming 5m/15m bar from the closed 1m bars inside it
        try:
            m1 = [b for b in _bulk_klines(sym, "1m") if b[0] >= now]
            if m1: bars.append((now, m1[0][1], max(b[2] for b in m1), min(b[3] for b in m1), m1[-1][4]))
        except Exception: pass
    if bars and mark and h:                                  # live tick from the bot's BULK feed onto the forming bar
        t, o, hi, lo, c = bars[-1]
        bars[-1:] = [(t, o, max(hi, mark), min(lo, mark), mark)] if t == now else [bars[-1], (now, c, max(c, mark), min(c, mark), mark)]
    t0 = bars[0][0] if bars else time.time() - sec * 240
    c = _db(); T = _rows(c, "SELECT * FROM trades WHERE closed >= ? ORDER BY closed", t0); c.close()
    mk, lines = chart_markers(T, h.get("positions") or [], h.get("quotes"), sym, t0, sec)
    return {"demo": False, "symbol": sym, "tf": tf, "source": "BULK klines + bot mark" if h else "BULK klines (bot offline)",
            "candles": [{"time": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4]} for b in bars], "markers": mk, "lines": lines,
            "mark": mark, "updated": int(time.time())}

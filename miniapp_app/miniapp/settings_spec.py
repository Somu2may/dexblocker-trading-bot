"""Settings the Mini App may read/write. Anything not listed (mode, paper balance, fees, data sources, keys) is untouchable."""
SYMBOLS = ["BTC", "ETH", "SOL"]   # default; validate() accepts any symbol listed in config `symbols`
FIELDS = [
 {"key": "risk.risk_pct", "label": "Risk per trade", "unit": "% of equity (stop distance + fees)", "type": "float", "min": 0.1, "max": 2, "step": 0.1, "group": "Risk"},
 {"key": "risk.leverage", "label": "Max leverage", "unit": "x (hard cap 3x)", "type": "float", "min": 1, "max": 3, "step": 0.5, "group": "Risk"},
 {"key": "risk.max_open_positions", "label": "Max open positions", "unit": "one per coin", "type": "int", "min": 1, "max": 4, "step": 1, "group": "Risk"},
 {"key": "risk.daily_loss_cap_pct", "label": "Daily loss cap", "unit": "% equity, pauses until IST midnight", "type": "float", "min": 0.5, "max": 10, "step": 0.5, "group": "Risk"},
 {"key": "risk.max_consecutive_losses", "label": "Loss-streak breaker", "unit": "losses in a row", "type": "int", "min": 2, "max": 10, "step": 1, "group": "Risk"},
 {"key": "exits.tp1_r", "label": "TP1", "unit": "R multiple (closes 50%, stop → breakeven)", "type": "float", "min": 0.5, "max": 3, "step": 0.1, "group": "Scalper only (inactive while Donchian runs)"},
 {"key": "exits.trail_atr_mult", "label": "Trailing stop", "unit": "x ATR after TP1", "type": "float", "min": 0.5, "max": 5, "step": 0.1, "group": "Scalper only (inactive while Donchian runs)"},
 {"key": "exits.max_hold_min", "label": "Max hold", "unit": "minutes", "type": "int", "min": 5, "max": 480, "step": 5, "group": "Scalper only (inactive while Donchian runs)"},
 {"key": "strategy.adx_min", "label": "Min ADX (chop filter)", "unit": "entry timeframe", "type": "float", "min": 10, "max": 40, "step": 1, "group": "Scalper only (inactive while Donchian runs)"},
 {"key": "report.every_days", "label": "Auto Telegram report", "unit": "every N days at report.at_ist (20:00 IST); 0 = off", "type": "int", "min": 0, "max": 14, "step": 1, "group": "Auto report"},
 {"key": "strategy.gmgn.position_size_pct", "label": "Memecoin size", "unit": "% of paper equity (1-2)", "type": "float", "min": 1, "max": 2, "step": 0.1, "group": "Memecoin (gmgn_meme)"},
 {"key": "strategy.gmgn.stop_loss_pct", "label": "Memecoin stop", "unit": "% below fill", "type": "float", "min": 1, "max": 50, "step": 1, "group": "Memecoin (gmgn_meme)"},
 {"key": "strategy.gmgn.max_open_positions", "label": "Memecoin max open", "unit": "positions", "type": "int", "min": 1, "max": 8, "step": 1, "group": "Memecoin (gmgn_meme)"},
 {"key": "strategy.gmgn.max_chase_pct", "label": "Max chase", "unit": "1h % already up", "type": "float", "min": 5, "max": 300, "step": 5, "group": "Memecoin (gmgn_meme)"},
 {"key": "strategy.gmgn.min_smart_buyers", "label": "Smart-money buyers", "unit": "distinct wallets", "type": "int", "min": 2, "max": 10, "step": 1, "group": "Memecoin (gmgn_meme)"},
 {"key": "strategy.gmgn.max_top10_pct", "label": "Max top-10", "unit": "% of supply", "type": "float", "min": 5, "max": 80, "step": 1, "group": "Memecoin (gmgn_meme)"},
 {"key": "enabled_symbols", "label": "Coins", "unit": "auto-trade on/off", "type": "symbols", "group": "Coins"},
]
BY_KEY = {f["key"]: f for f in FIELDS}

def validate(key, v, allowed_symbols=None):
    f = BY_KEY.get(key)
    if not f: raise ValueError(f"{key} not editable")
    t = f["type"]
    if t == "bool":
        if not isinstance(v, bool): raise ValueError(f"{key}: need true/false")
        return v
    if t == "symbols":
        allowed = allowed_symbols or SYMBOLS
        if not isinstance(v, list) or any(s not in allowed for s in v): raise ValueError("coins must be a list of " + ", ".join(allowed))
        return [s for s in allowed if s in v]
    x = float(v)
    if not f["min"] <= x <= f["max"]: raise ValueError(f"{f['label']}: {f['min']}-{f['max']}")
    return int(x) if t == "int" else x

def get(cfg, key):
    node = cfg
    for p in key.split("."):
        if not isinstance(node, dict) or p not in node: return None
        node = node[p]
    return node

def put(cfg, key, v):
    parts = key.split("."); node = cfg
    for p in parts[:-1]: node = node.setdefault(p, {})
    node[parts[-1]] = v

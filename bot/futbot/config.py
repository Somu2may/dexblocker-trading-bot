import os, re, yaml, threading, tempfile
PATH = os.environ.get("MEMEBOT_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml"))
DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml")
_lock = threading.Lock()
LOCKED_PREFIX = ("mode", "paper.", "data.sources", "fees.", "defaults_rev")
NAMES = ("gmgn_meme", "hf_maker", "donchian_15m", "donchian_1h", "scalper")

def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

# One-time moderate loosening. Does not bump defaults_rev (that would also force strategy.name).
# A live file is updated only while strategy.gmgn.gates_rev is below this, and only for keys
# that still equal the previous shipped number. Hand-edited values stay.
GATES_REV = 2
GATE_STEPS = (
    ("min_liquidity_usd", 15000, 8000),
    ("min_volume_usd", 10000, 6000),
    ("nokey_min_buy_sell_ratio", 1.3, 1.1),
    ("max_top10_pct", 40, 50),
    ("min_age_sec", 180, 120),          # 3 minutes -> 2 minutes
    ("max_age_sec", 172800, 72 * 3600), # 48 hours -> 72 hours
    ("min_momentum_pct", 5, 3),
    ("max_chase_pct", 80, 100),
)

def _num_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    try:
        return abs(float(a) - float(b)) <= 1e-9
    except (TypeError, ValueError):
        return False

def _fmt_num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)

def migrate_gmgn_gates(gmgn):
    """Mutate one gmgn dict. None means this rev is already applied. Otherwise the keys that moved."""
    if not isinstance(gmgn, dict):
        return None
    try:
        rev = int(gmgn.get("gates_rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    if rev >= GATES_REV:
        return None
    changed = []
    for key, old, new in GATE_STEPS:
        if key in gmgn and _num_eq(gmgn.get(key), old):
            gmgn[key] = new
            changed.append(key)
    gmgn["gates_rev"] = GATES_REV
    return changed

def _replace_scalar(text, key, new):
    pat = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*)([^\n#]*?)([ \t]*(#.*)?)$", re.M)
    def repl(m):
        return f"{m.group(1)}{_fmt_num(new)}{m.group(3) or ''}"
    return pat.subn(repl, text, count=1)

def _insert_gates_rev(text):
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"^(\s*)gmgn:\s*(#.*)?$", line):
            indent = "    "
            if i + 1 < len(lines):
                m = re.match(r"^(\s+)\S", lines[i + 1])
                if m:
                    indent = m.group(1)
            lines.insert(i + 1, f"{indent}gates_rev: {GATES_REV}\n")
            return "".join(lines)
    return None

def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)) or ".")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)

def persist_gmgn_gate_defaults(path):
    """Write the one-time gate migration into a live config. The repo default file is never rewritten.
    Returns the keys whose values changed, or None when this rev was already stamped."""
    if not path or not os.path.exists(path) or os.path.abspath(path) == os.path.abspath(DEFAULT):
        return None
    with _lock:
        with open(path) as f:
            text = f.read()
        try:
            live = yaml.safe_load(text) or {}
        except Exception:
            return None
        if not isinstance(live, dict) or not isinstance(live.get("strategy"), dict):
            return None
        g = live["strategy"].get("gmgn")
        if not isinstance(g, dict):
            return None
        changed = migrate_gmgn_gates(g)
        if changed is None:
            return None
        new_text, ok = text, True
        for key in changed:
            new_text, n = _replace_scalar(new_text, key, g[key])
            if n != 1:
                ok = False
                break
        if ok:
            if re.search(r"(?m)^\s*gates_rev\s*:", new_text):
                new_text, n = _replace_scalar(new_text, "gates_rev", GATES_REV)
                ok = n == 1
            else:
                inserted = _insert_gates_rev(new_text)
                if inserted is None:
                    ok = False
                else:
                    new_text = inserted
        if not ok:
            new_text = yaml.safe_dump(live, sort_keys=False)
        if new_text != text:
            _atomic_write(path, new_text)
        return changed

def load(path=None):
    path = path or PATH
    with open(DEFAULT) as f: base = yaml.safe_load(f)
    c = base
    if os.path.abspath(path) != os.path.abspath(DEFAULT) and os.path.exists(path):
        try:
            persist_gmgn_gate_defaults(path)
        except Exception:
            pass
        with open(path) as f: over = yaml.safe_load(f) or {}
        c = _merge(base, over)                                           # new keys from the repo default always present
        if int(over.get("defaults_rev") or 1) < int(base.get("defaults_rev") or 1):
            c["strategy"]["name"] = base["strategy"]["name"]             # one-time push of a new repo default strategy
            c["defaults_rev"] = base["defaults_rev"]
        live_g = (over.get("strategy") or {}).get("gmgn") if isinstance(over.get("strategy"), dict) else None
        try:
            stamped = int((live_g or {}).get("gates_rev") or 0)
        except (TypeError, ValueError):
            stamped = 0
        if stamped < GATES_REV and isinstance((c.get("strategy") or {}).get("gmgn"), dict):
            migrate_gmgn_gates(c["strategy"]["gmgn"])
    c["mode"] = "paper"                      # hard-locked: no live trading in this version
    r = c["risk"]; r["leverage"] = max(1.0, min(3.0, float(r.get("leverage", 3))))
    if c.get("strategy", {}).get("name") not in NAMES: c["strategy"]["name"] = NAMES[0]
    c["enabled_symbols"] = [s for s in c.get("enabled_symbols", c["symbols"]) if s in c["symbols"]]
    return c

def save(c, path=None):
    path = path or PATH
    c["mode"] = "paper"
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, "w") as f: yaml.safe_dump(c, f, sort_keys=False)
        os.replace(tmp, path)

def set_key(c, dotted, raw):
    """/set strategy.adx_min 22 - only existing keys, type follows the old value; mode/paper/fees/sources locked."""
    if dotted == "mode" or dotted.startswith(LOCKED_PREFIX): raise ValueError(f"{dotted} is locked")
    parts = dotted.split("."); node = c
    for p in parts[:-1]:
        if p not in node or not isinstance(node[p], dict): raise KeyError(dotted)
        node = node[p]
    k = parts[-1]
    if k not in node: raise KeyError(dotted)
    old = node[k]
    if isinstance(old, bool): v = str(raw).lower() in ("1", "true", "yes", "on")
    elif isinstance(old, int) and not isinstance(old, bool): v = int(float(raw))
    elif isinstance(old, float): v = float(raw)
    elif isinstance(old, list):
        v = yaml.safe_load(raw) if isinstance(raw, str) else raw
        if not isinstance(v, list): raise ValueError("need a list like [BTC, ETH]")
    else: v = raw
    if dotted == "strategy.name" and v not in NAMES: raise ValueError("strategy.name must be one of " + ", ".join(NAMES))
    if dotted == "report.at_ist":
        hh, _, mm = str(v).partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60): raise ValueError("report.at_ist must be HH:MM (IST)")
        v = f"{int(hh):02d}:{int(mm):02d}"
    if dotted == "report.every_days": v = max(0, min(14, int(v)))
    if dotted == "risk.leverage": v = max(1.0, min(3.0, float(v)))
    if dotted == "risk.risk_pct": v = max(0.1, min(2.0, float(v)))
    if dotted == "strategy.gmgn.position_size_pct": v = max(1.0, min(2.0, float(v)))
    if dotted == "strategy.gmgn.stop_loss_pct": v = max(1.0, min(50.0, float(v)))
    if dotted == "strategy.gmgn.min_smart_buyers": v = max(2, min(10, int(v)))
    node[k] = v
    return v

def flatten(c, prefix=""):
    out = {}
    for k, v in c.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict): out.update(flatten(v, key + "."))
        else: out[key] = v
    return out

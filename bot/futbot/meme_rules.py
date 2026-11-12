"""Pure gates, entry rule, dev-history numbers and wallet-style tags for gmgn_meme.

Fail-closed: a missing or non-numeric measurement REJECTS. A price quote is not
evidence that a sell would land. Display scores never override a failed gate.

Reimplemented ideas (not copied code) from GMGN's public aitrader demo:
dev rug rate and how many of the dev's tokens are still alive, explicit token
security, wallet-style tags, and an entry market-cap band. The demo treats a
missing honeypot flag as safe and a missing dev profile as neutral. This module
does not.
"""

# What is actually measured. Kept next to the checks so the scanner, /status and docs stay aligned.
HONESTY = {
    "mint_authority": "GMGN renounced_mint, else RugCheck token.mintAuthority null. Missing or sources disagree => reject.",
    "freeze_authority": "GMGN renounced_freeze_account, else RugCheck freezeAuthority null. Missing or disagree => reject.",
    "lp": "GMGN burn_status/burn_ratio, else RugCheck LP locked percent. Unknown => reject. A pool address alone is not a lock.",
    "top10": "GMGN top_10_holder_rate, else the sum of RugCheck or GMGN top-holder percents. Unknown => reject.",
    "dev_history": "Creator token list: alive = still open and not flagged liquidity-drained; rug rate = 1 - alive/analyzed. A launch count with no per-token list is NOT enough and rejects.",
    "dev_exited": "When the payload says the creator balance is ~0 and status is closed/cleared, reject. Unknown does not pass this subcheck by itself.",
    "dev_holding": "GMGN dev_team_hold_rate or creator balance rate. Unknown => reject.",
    "bundled": "GMGN bundler_rate or bundler wallet count. Unknown => reject.",
    "snipers": "GMGN sniper_count (launch snipers). Unknown => reject. This is not the same as a wallet-style 'sniper' tag on later trades.",
    "honeypot": "GMGN mode: PASS only when is_honeypot is explicitly no/false. Empty string (common on SOL) is UNKNOWN and rejects. RugCheck honeypot/cannot-sell risk rejects. Absence of the word honeypot is not a GMGN pass. No-key mode: a RugCheck risk list with no honeypot/cannot-sell flag passes; a missing risk list rejects. A DexScreener price or a swap quote does not prove the token can be sold.",
    "sell_tax": "GMGN sell_tax number, or RugCheck transfer-fee percent, or a legacy SPL mint whose report includes transferFee null (that program cannot charge a transfer fee). Unknown => reject.",
    "liquidity": "GMGN pool liquidity or DexScreener liquidity.usd. Unknown => reject.",
    "volume": "GMGN window volume or DexScreener volume (1h preferred; 24h is labeled as 24h). Unknown => reject.",
    "token_age": "GMGN creation/open timestamp or DexScreener pairCreatedAt. Unknown => reject.",
    "mcap": "GMGN market_cap or DexScreener marketCap/fdv. Unknown => reject. This is the entry market-cap band.",
    "smart_money": "GMGN mode: distinct wallet addresses tagged smart money (or in the GMGN smartmoney list) with buy volume above sell volume. If addresses are absent, GMGN smart_degen_count is an aggregate only and is labeled as such. Neither => reject. No-key mode skips this and uses the h1 buy/sell txn ratio plus 5m buys >= sells instead.",
    "rug_danger": "No-key mode: any RugCheck risk whose level is danger rejects. A missing risk list rejects. Warn-only risks do not fail this gate by themselves.",
    "momentum": "1h and 5m price change plus buy/sell counts. 1h above max_chase rejects. A 5m jump above max_m5_spike_pct rejects (chasing a spike). Any missing piece rejects. DexScreener priceChange/txns can supply this; they still say nothing about honeypot.",
    "impersonation": "Symbol or name that copies a major (BTC, ETH, SOL, USDC, USDT, and the usual large-cap tickers) rejects. A memecoin ticker that is not that major passes.",
}


def _num(v):
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def as_frac(v):
    """0-1 fraction. Values in (1, 100] are treated as percent points. Garbage => None."""
    x = _num(v)
    if x is None or x < 0:
        return None
    if x <= 1:
        return x
    if x <= 100:
        return x / 100.0
    return None


def tri(v):
    """Explicit bool. True/False/1/0/yes/no. Anything else, including '', is unknown (None)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes"):
            return True
        if s in ("0", "false", "no"):
            return False
    return None


def merge_tri(a, b):
    """Combine two explicit bools. None is unknown. Disagreement is 'conflict' (fail)."""
    if a is None:
        return b
    if b is None:
        return a
    if a == "conflict" or b == "conflict" or a != b:
        return "conflict"
    return a


def dev_from_tokens(tokens, extra=None):
    """Per-token creator history. tokens items: is_open, liquidity_less_4k, logo, token_address.
    Without a list, rug_rate stays None (fail-closed). A lone open_count is not survival."""
    extra = extra or {}
    rows = [t for t in (tokens or []) if isinstance(t, dict)]
    out = {
        "creator": extra.get("creator") or "",
        "open_count": extra.get("open_count"),
        "inner_count": extra.get("inner_count"),
        "analyzed": 0,
        "alive": None,
        "rugged": None,
        "rug_rate": None,
        "own_img_reuse": None,
        "exited": extra.get("exited"),
        "source": None,
    }
    if not rows:
        return out
    classified = []
    for t in rows:
        if "is_open" not in t and "open" not in t:
            continue
        opened = bool(t.get("is_open") if "is_open" in t else t.get("open"))
        drained = bool(t.get("liquidity_less_4k"))
        if t.get("liquidity") is not None:
            try:
                drained = drained or float(t["liquidity"]) < 4000
            except (TypeError, ValueError):
                pass
        classified.append(opened and not drained)
    if not classified:
        return out
    alive = sum(1 for x in classified if x)
    total = len(classified)
    logos = [t.get("logo") for t in rows if t.get("logo")]
    out.update(analyzed=total, alive=alive, rugged=total - alive,
               rug_rate=(total - alive) / total,
               own_img_reuse=(max(0, len(logos) - len(set(logos))) if logos else 0),
               source="created_tokens")
    return out


def dev_exited_from_status(status, balance):
    bal = _num(balance)
    st = str(status or "")
    if bal is None and not st:
        return None
    if bal is not None and bal <= 0 and any(s in st for s in ("close", "clear", "sell")):
        return True
    if bal is not None and bal > 0:
        return False
    return None


def wallet_style_tags(stats, activity):
    """Labels for a wallet payload. They do not pass a token gate.
    stats keys: trades, token_num, winrate, avg_hold_s, avg_buy_usd, realized_profit,
    created_token_count, dist{lt_n50, gt_5, x2_5}.
    activity keys: entry_under_100k (0-1), fast_flip_rate (0-1), median_entry_mcap.
    """
    if not stats:
        return []
    tags = []
    trades = int(stats.get("trades") or 0)
    tn = max(1, int(stats.get("token_num") or 0))
    dist = stats.get("dist") or {}
    big_loss = float(dist.get("lt_n50") or 0) / tn
    hold = float(stats.get("avg_hold_s") or 0)
    early = float((activity or {}).get("entry_under_100k") or 0)
    flip = float((activity or {}).get("fast_flip_rate") or 0)
    med = float((activity or {}).get("median_entry_mcap") or 0)
    created = int(stats.get("created_token_count") or 0)
    is_dev = created > 0 and created * 2 >= tn
    if is_dev:
        tags.append("dev")
    if trades >= 2000:
        tags.append("bot")
    if flip >= 0.3:
        tags.append("flash")
    if not is_dev and early >= 0.8:
        tags.append("sniper")
    if hold >= 5 * 86400 and trades < 200:
        tags.append("diamond")
    if float(stats.get("avg_buy_usd") or 0) >= 5000:
        tags.append("whale")
    if not is_dev and float(stats.get("winrate") or 0) >= 0.65 and trades >= 15:
        tags.append("high_win")
    if big_loss >= 0.3 and float(stats.get("realized_profit") or 0) < 0:
        tags.append("bagholder")
    if med and med < 30000 and not is_dev:
        tags.append("small_mcap")
    return tags


def display_score(facts, dev):
    """0-99 momentum/safety sketch for the scanner. None when 1h change is missing.
    Never used as a pass/fail. Missing dev history omits that term (it does not score 0.5)."""
    chg = facts.get("chg_1h")
    if chg is None:
        return None
    chg5 = facts.get("chg_5m") if facts.get("chg_5m") is not None else 0.0
    buys, sells = facts.get("buys"), facts.get("sells")
    ratio = (buys / (buys + sells)) if buys is not None and sells is not None and (buys + sells) > 0 else 0.5
    mcap = facts.get("mcap") or 0
    vol = facts.get("volume") or 0
    turn = (vol / mcap) if mcap else 0
    buyers = facts.get("smart_buyers")
    nsm = len(set(buyers)) if isinstance(buyers, list) else (facts.get("smart_degen_count") or 0)
    top10 = facts.get("top10")
    mint_ok = facts.get("mint_revoked") is True and facts.get("freeze_revoked") is True

    def clamp(x):
        return 0.0 if x < 0 else 1.0 if x > 1 else x

    s_mom5 = clamp((chg5 / 100.0 + 0.05) / 0.30) if facts.get("chg_5m") is not None else 0.0
    s_mom1 = clamp((chg / 100.0 + 0.10) / 0.60)
    s_buy = clamp((ratio - 0.40) / 0.30)
    s_turn = clamp(turn / 3.0)
    s_cons = clamp(__import__("math").log10(1 + nsm) / 2.5)
    s_safe = (0.5 if mint_ok else 0.0) + (0.5 * clamp((0.40 - top10) / 0.40) if top10 is not None else 0.0)
    # weights sum to 94 without dev, +12 with dev, then scaled into 0-99
    s = 30 * s_mom5 + 12 * s_mom1 + 18 * s_buy + 12 * s_turn + 12 * s_cons + 10 * s_safe
    if dev and dev.get("rug_rate") is not None:
        s += 12 * clamp(1.0 - float(dev["rug_rate"]))
        s = s / 106 * 99
    else:
        s = s / 94 * 99
    if chg <= 0:
        s *= 0.4
    return max(0, min(99, int(round(s))))


def _fail(code, detail, source=None):
    return {"code": code, "ok": False, "detail": detail, "verified_by": source, "honesty": HONESTY.get(code, "")}


def _ok(code, detail, source):
    return {"code": code, "ok": True, "detail": detail, "verified_by": source, "honesty": HONESTY.get(code, "")}


def _src(facts, key):
    return (facts.get("sources") or {}).get(key)


def is_nokey(mode):
    """True for the DexScreener + RugCheck path used when GMGN_API_KEY is absent."""
    return str(mode or "").lower() in ("nokey", "no-key", "no_key")


# Measured only by GMGN. No-key mode records these as skipped instead of rejecting.
GMGN_ONLY_GATES = ("dev_history", "dev_exited", "dev_holding", "bundled", "snipers")


def _skip(code):
    return {"code": code, "ok": True, "skipped": True,
            "detail": "skipped in no-key mode (GMGN-only)", "verified_by": None,
            "honesty": HONESTY.get(code, "")}


def market_ready(facts, g):
    """DexScreener gates that can be decided before spending a RugCheck call.
    False means liquidity, volume, age, or mcap is already a reject."""
    f = facts or {}
    g = g or {}
    liq, vol, age, mcap = f.get("liquidity"), f.get("volume"), f.get("age_sec"), f.get("mcap")
    if liq is None or vol is None or age is None or mcap is None:
        return False
    if liq < float(g.get("min_liquidity_usd", 8000)):
        return False
    if vol < float(g.get("min_volume_usd", 6000)):
        return False
    lo, hi = float(g.get("min_age_sec", 120)), float(g.get("max_age_sec", 72 * 3600))
    if age < lo or age > hi:
        return False
    mlo, mhi = float(g.get("min_mcap_usd", 8000)), float(g.get("max_mcap_usd", 2_000_000))
    if mcap < mlo or mcap > mhi:
        return False
    return True


MAJORS = frozenset({
    "BTC", "ETH", "SOL", "USDC", "USDT", "BNB", "XRP", "DOGE", "ADA", "TRX", "TON",
    "AVAX", "DOT", "LINK", "MATIC", "POL", "DAI", "WBTC", "WETH", "WSOL", "BUSD",
    "TUSD", "FDUSD", "PYUSD", "USDE", "LTC", "BCH",
})
MAJOR_NAMES = frozenset({
    "BITCOIN", "ETHEREUM", "SOLANA", "TETHER", "USDCOIN", "BINANCE", "BINANCECOIN",
    "RIPPLE", "DOGECOIN", "CARDANO", "TRON", "AVALANCHE", "POLKADOT", "CHAINLINK",
})
_MAJOR_SUFFIX = ("SOL", "USD", "USDC", "USDT")


def _norm_token(s):
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


def is_major_impersonation(symbol, name=""):
    """True when the ticker or name is a major, not a different word that merely contains those letters."""
    sym = _norm_token(symbol)
    if sym in MAJORS or sym in MAJOR_NAMES:
        return True
    for major in MAJORS:
        if any(sym == major + suf for suf in _MAJOR_SUFFIX):
            return True
    full = _norm_token(name)
    if full in MAJORS or full in MAJOR_NAMES:
        return True
    for part in str(name or "").replace("/", " ").replace("-", " ").replace("_", " ").split():
        token = _norm_token(part)
        if token in MAJORS or token in MAJOR_NAMES:
            return True
    return False


def m5_spike_reason(chg5, g):
    """Reject a fresh upward jump. None when the 5m change is missing or inside the cap."""
    if chg5 is None:
        return None
    cap = float((g or {}).get("max_m5_spike_pct", 25))
    if float(chg5) > cap:
        return f"m5 spike {float(chg5):.1f}% > {cap:.0f}% (not chasing a jump)"
    return None


def quote_entry_block(facts, g, now):
    """Reject an entry quote older than max_quote_age_sec. Missing timestamp rejects."""
    g = g or {}
    ts = (facts or {}).get("quote_ts")
    if ts is None:
        return False, "quote timestamp unknown"
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return False, "quote timestamp unknown"
    if ts > 1e12:
        ts = ts / 1000.0
    age = float(now) - ts
    cap = float(g.get("max_quote_age_sec", 30))
    if age > cap:
        return False, f"quote stale {age:.0f}s > {cap:.0f}s"
    if age < -5:
        return False, "quote timestamp is in the future"
    return True, "ok"


def stamp_quote(facts, now, seen):
    """Age an unchanged DexScreener print from the first time we saw it.

    A payload timestamp wins. Otherwise the same price and 5m/h1 flow keeps the
    earlier observation time, so a cached pair ages out of the entry window.
    `seen` maps mint -> (fingerprint, first_seen_ts).
    """
    if not isinstance(facts, dict):
        return facts
    src = facts.get("quote_ts")
    fp = (facts.get("price"), facts.get("chg_5m"), facts.get("buys"), facts.get("sells"))
    mint = facts.get("mint")
    prev = seen.get(mint) if mint else None
    if src is not None:
        try:
            ts = float(src)
            if ts > 1e12:
                ts = ts / 1000.0
            facts["quote_ts"] = ts
        except (TypeError, ValueError):
            facts["quote_ts"] = now
    elif prev and prev[0] == fp:
        facts["quote_ts"] = prev[1]
    else:
        facts["quote_ts"] = now
    if mint:
        if src is None and prev and prev[0] == fp:
            first = prev[1]
        else:
            first = facts["quote_ts"]
        seen[mint] = (fp, first)
    return facts


def entry_metrics(facts):
    """One line of entry context stored on the paper trade and the signal log."""
    f = facts or {}

    def fmt(v, nd=2):
        x = v if isinstance(v, (int, float)) and v == v else None
        if x is None:
            return "-"
        if nd == 0:
            return str(int(round(x)))
        return f"{x:.{nd}f}"

    buys, sells = f.get("buys"), f.get("sells")
    ratio = None
    if buys is not None and sells is not None:
        try:
            if int(buys) + int(sells) > 0:
                ratio = int(buys) / max(1, int(sells))
        except (TypeError, ValueError):
            ratio = None
    top = f.get("top10")
    top_pct = None
    if isinstance(top, (int, float)) and top == top:
        top_pct = top * 100.0 if top <= 1.5 else top
    return " ".join((
        f"liq={fmt(f.get('liquidity'), 0)}",
        f"mcap={fmt(f.get('mcap'), 0)}",
        f"age_s={fmt(f.get('age_sec'), 0)}",
        f"h1={fmt(f.get('chg_1h'))}",
        f"m5={fmt(f.get('chg_5m'))}",
        f"bs={fmt(ratio)}",
        f"rug={fmt(f.get('rug_score'), 0)}",
        f"top10={fmt(top_pct)}",
    ))


def run_gates(facts, g, mode="gmgn"):
    """Every hard gate. Unknown => fail. Percents in `g` are human percent points (40 = 40%).
    mode "nokey" skips GMGN-only gates and reads honeypot from the RugCheck risk list."""
    f = facts or {}
    src = f.get("sources") or {}
    out = []
    conflicts = set(f.get("conflicts") or [])

    def tri_gate(code, key, good_detail, bad_detail):
        v = f.get(key)
        if key in conflicts or v == "conflict":
            out.append(_fail(code, f"{code}: sources disagree", src.get(key)))
        elif v is True:
            out.append(_ok(code, good_detail, src.get(key)))
        elif v is False:
            out.append(_fail(code, bad_detail, src.get(key)))
        else:
            out.append(_fail(code, f"{code} unknown", None))

    tri_gate("mint_authority", "mint_revoked", "mint authority revoked", "mint authority still set")
    tri_gate("freeze_authority", "freeze_revoked", "freeze authority revoked", "freeze authority still set")

    burn = f.get("burn_ratio")
    if burn is None and is_nokey(mode):
        burn = f.get("rug_burn_ratio")
    lock = f.get("lp_locked_pct")
    min_burn = float(g.get("min_burn_pct", 95)) / 100.0
    min_lock = float(g.get("min_lp_locked_pct", 80))
    if burn is None and lock is None:
        out.append(_fail("lp", "LP burn/lock unknown", None))
    elif (burn is not None and burn >= min_burn) or (lock is not None and lock >= min_lock):
        bit = []
        if burn is not None:
            bit.append(f"burn {burn:.0%}")
        if lock is not None:
            bit.append(f"locked {lock:.0f}%")
        out.append(_ok("lp", ", ".join(bit), src.get("lp") or src.get("burn_ratio") or src.get("rug_burn_ratio") or src.get("lp_locked_pct")))
    else:
        out.append(_fail("lp", f"LP not burned/locked (burn {burn}, locked {lock})", src.get("lp")))

    top = f.get("top10")
    max_top = float(g.get("max_top10_pct", 50)) / 100.0
    if top is None:
        out.append(_fail("top10", "top-10 holder share unknown", None))
    elif top > max_top:
        out.append(_fail("top10", f"top10 {top:.0%} > {max_top:.0%}", src.get("top10")))
    else:
        out.append(_ok("top10", f"top10 {top:.0%}", src.get("top10")))

    dev = f.get("dev") or {}
    if is_nokey(mode):
        for code in GMGN_ONLY_GATES:
            out.append(_skip(code))
    else:
        max_rug = float(g.get("max_dev_rug_pct", 50)) / 100.0
        if not dev or dev.get("rug_rate") is None or not dev.get("analyzed"):
            out.append(_fail("dev_history", "dev history unknown (need the creator's token list, not just a launch count)", None))
        elif dev["rug_rate"] > max_rug:
            out.append(_fail("dev_history", f"dev rug rate {dev['rug_rate']:.0%} ({dev.get('alive')}/{dev.get('analyzed')} still alive)", dev.get("source")))
        else:
            out.append(_ok("dev_history", f"dev alive {dev.get('alive')}/{dev.get('analyzed')} (rug {dev['rug_rate']:.0%})", dev.get("source")))
        reuse = dev.get("own_img_reuse")
        max_re = int(g.get("max_reskin", 2))
        if reuse is not None and reuse > max_re:
            out.append(_fail("dev_history", f"dev reused artwork on {reuse} launches", dev.get("source")))
        inner = dev.get("inner_count")
        max_inner = int(g.get("max_inner_count", 300))
        if inner is not None and int(inner) > max_inner:
            out.append(_fail("dev_history", f"dev inner/unmigrated launches {inner} > {max_inner}", dev.get("source")))
        if dev.get("exited") is True:
            out.append(_fail("dev_exited", "dev has exited this token", dev.get("source") or src.get("dev")))

        hold = f.get("dev_hold")
        max_hold = float(g.get("max_dev_hold_pct", 10)) / 100.0
        if hold is None:
            out.append(_fail("dev_holding", "dev holding unknown", None))
        elif hold > max_hold:
            out.append(_fail("dev_holding", f"dev holds {hold:.0%} > {max_hold:.0%}", src.get("dev_hold")))
        else:
            out.append(_ok("dev_holding", f"dev holds {hold:.0%}", src.get("dev_hold")))

        br = f.get("bundler_rate")
        bw = f.get("bundler_wallets")
        max_b = float(g.get("max_bundler_pct", 30)) / 100.0
        max_bw = int(g.get("max_bundler_wallets", 8))
        if br is None and bw is None:
            out.append(_fail("bundled", "bundler share unknown", None))
        elif (br is not None and br > max_b) or (bw is not None and bw > max_bw):
            out.append(_fail("bundled", f"bundlers rate {br} wallets {bw}", src.get("bundler_rate")))
        else:
            out.append(_ok("bundled", f"bundlers rate {br} wallets {bw}", src.get("bundler_rate")))

        sn = f.get("sniper_count")
        max_sn = int(g.get("max_sniper_wallets", 20))
        if sn is None:
            out.append(_fail("snipers", "sniper count unknown", None))
        elif int(sn) > max_sn:
            out.append(_fail("snipers", f"snipers {int(sn)} > {max_sn}", src.get("sniper_count")))
        else:
            out.append(_ok("snipers", f"snipers {int(sn)}", src.get("sniper_count")))

    hp = f.get("honeypot")
    if is_nokey(mode):
        # RugCheck risk flags. An explicit GMGN "no" is not required. A missing list is unknown.
        if hp == "conflict" or "honeypot" in conflicts:
            out.append(_fail("honeypot", "honeypot sources disagree", src.get("honeypot")))
        elif hp is True:
            out.append(_fail("honeypot", "RugCheck honeypot / cannot-sell risk", src.get("honeypot") or "rugcheck"))
        elif f.get("rug_risks_seen") is True:
            out.append(_ok("honeypot", "RugCheck risk list has no honeypot / cannot-sell flag (this is not a simulated sell)", src.get("honeypot") or "rugcheck"))
        else:
            out.append(_fail("honeypot", "honeypot unknown (no RugCheck risk list; a price quote does not prove the token can be sold)", None))
        dangers = f.get("rug_dangers")
        if f.get("rug_risks_seen") is not True or dangers is None:
            out.append(_fail("rug_danger", "RugCheck risk list unknown", None))
        elif dangers:
            names = ", ".join(str(x) for x in dangers[:6])
            out.append(_fail("rug_danger", f"RugCheck danger: {names}", "rugcheck"))
        else:
            out.append(_ok("rug_danger", "no RugCheck danger risk", "rugcheck"))
    else:
        if hp == "conflict" or "honeypot" in conflicts:
            out.append(_fail("honeypot", "honeypot sources disagree", src.get("honeypot")))
        elif hp is True:
            out.append(_fail("honeypot", "honeypot flag is set", src.get("honeypot")))
        elif hp is False:
            out.append(_ok("honeypot", "security source explicitly says not a honeypot (this is not a simulated sell)", src.get("honeypot")))
        else:
            out.append(_fail("honeypot", "honeypot unknown (a price quote does not prove the token can be sold)", None))

    tax = f.get("sell_tax")
    max_tax = float(g.get("max_sell_tax_pct", 5)) / 100.0
    if tax is None:
        out.append(_fail("sell_tax", "sell tax unknown", None))
    elif tax > max_tax:
        out.append(_fail("sell_tax", f"sell tax {tax:.1%} > {max_tax:.1%}", src.get("sell_tax")))
    else:
        out.append(_ok("sell_tax", f"sell tax {tax:.1%}", src.get("sell_tax")))

    liq = f.get("liquidity")
    min_liq = float(g.get("min_liquidity_usd", 8000))
    if liq is None:
        out.append(_fail("liquidity", "liquidity unknown", None))
    elif liq < min_liq:
        out.append(_fail("liquidity", f"liquidity ${liq:,.0f} < ${min_liq:,.0f}", src.get("liquidity")))
    else:
        out.append(_ok("liquidity", f"liquidity ${liq:,.0f}", src.get("liquidity")))

    vol = f.get("volume")
    min_vol = float(g.get("min_volume_usd", 6000))
    window = f.get("volume_window") or "?"
    if vol is None:
        out.append(_fail("volume", "volume unknown", None))
    elif vol < min_vol:
        out.append(_fail("volume", f"{window} volume ${vol:,.0f} < ${min_vol:,.0f}", src.get("volume")))
    else:
        out.append(_ok("volume", f"{window} volume ${vol:,.0f}", src.get("volume")))

    age = f.get("age_sec")
    lo, hi = float(g.get("min_age_sec", 120)), float(g.get("max_age_sec", 72 * 3600))
    if age is None:
        out.append(_fail("token_age", "token age unknown", None))
    elif age < lo:
        out.append(_fail("token_age", f"age {age:.0f}s < {lo:.0f}s", src.get("age")))
    elif age > hi:
        out.append(_fail("token_age", f"age {age / 3600:.1f}h > {hi / 3600:.0f}h", src.get("age")))
    else:
        out.append(_ok("token_age", f"age {age / 60:.0f}m", src.get("age")))

    mcap = f.get("mcap")
    mlo, mhi = float(g.get("min_mcap_usd", 8000)), float(g.get("max_mcap_usd", 2_000_000))
    if mcap is None:
        out.append(_fail("mcap", "market cap unknown", None))
    elif mcap < mlo or mcap > mhi:
        out.append(_fail("mcap", f"mcap ${mcap:,.0f} outside ${mlo:,.0f}-${mhi:,.0f}", src.get("mcap")))
    else:
        out.append(_ok("mcap", f"mcap ${mcap:,.0f}", src.get("mcap")))

    if is_major_impersonation(f.get("symbol"), f.get("name")):
        out.append(_fail("impersonation", f"ticker copies a major ({f.get('symbol') or f.get('name')})", None))
    else:
        out.append(_ok("impersonation", "not a major ticker", None))
    return out


def _entry_nokey(facts, g):
    """Replace the smart-money signal: h1 buy/sell >= 1.1, 5m buys >= sells,
    1h change inside +min..+max chase, 5m not rolled over. Missing => reject."""
    f = facts or {}
    g = g or {}
    buys, sells = f.get("buys"), f.get("sells")
    if buys is None or sells is None:
        return False, "h1 buy/sell unknown"
    if int(buys) + int(sells) <= 0:
        return False, "no trades in the h1 window"
    ratio = int(buys) / max(1, int(sells))
    need_r = float(g.get("nokey_min_buy_sell_ratio", 1.1))
    if ratio < need_r - 1e-9:
        return False, f"h1 buy/sell {ratio:.2f} < {need_r}"
    b5, s5 = f.get("buys_5m"), f.get("sells_5m")
    if b5 is None or s5 is None:
        return False, "5m buy/sell unknown"
    if int(b5) < int(s5):
        return False, f"5m buys {int(b5)} below sells {int(s5)}"
    chg, chg5 = f.get("chg_1h"), f.get("chg_5m")
    max_chase = float(g.get("max_chase_pct", 100))
    min_mom = float(g.get("min_momentum_pct", 3))
    if chg is None:
        return False, "max-chase unknown (no 1h change)"
    if chg > max_chase:
        return False, f"max chase 1h {chg:.1f}% > {max_chase:.0f}%"
    if chg < min_mom:
        return False, f"1h momentum {chg:.1f}% < {min_mom:.0f}%"
    if chg5 is None:
        return False, "5m change unknown"
    floor = float(g.get("rollover_5m_pct", -3))
    if chg5 < floor:
        return False, f"5m momentum rolled over ({chg5:.1f}%)"
    spike = m5_spike_reason(chg5, g)
    if spike:
        return False, spike
    return True, (f"no-key flow h1 buy/sell {ratio:.2f}, 5m buys {int(b5)}>={int(s5)}, "
                  f"1h {chg:.1f}%, 5m {chg5:.1f}%")


def entry_rule(facts, g, mode="gmgn"):
    """GMGN: 2+ distinct smart-money wallets buying, momentum, and a max-chase cap.
    No-key: h1/5m txn flow instead of smart money. Returns (ok, reason)."""
    if is_nokey(mode):
        return _entry_nokey(facts, g)
    f = facts or {}
    need = int(g.get("min_smart_buyers", 2))
    buyers = f.get("smart_buyers")
    if isinstance(buyers, list):
        n = len(set(buyers))
        how = "distinct buy wallets"
    elif f.get("smart_degen_count") is not None:
        n = int(f["smart_degen_count"])
        how = "GMGN smart_degen_count aggregate (addresses not in this payload)"
    else:
        return False, "smart money unknown"
    if n < need:
        return False, f"smart money {n} < {need} ({how})"

    chg, chg5 = f.get("chg_1h"), f.get("chg_5m")
    max_chase = float(g.get("max_chase_pct", 100))
    min_mom = float(g.get("min_momentum_pct", 3))
    min_5 = float(g.get("min_momentum_5m_pct", 0))
    if chg is None:
        return False, "max-chase unknown (no 1h change)"
    if chg > max_chase:
        return False, f"max chase 1h {chg:.1f}% > {max_chase:.0f}%"
    if chg5 is None:
        return False, "momentum unknown (no 5m change)"
    if chg < min_mom and chg5 < min_5:
        return False, f"momentum not confirmed (1h {chg:.1f}%, 5m {chg5:.1f}%)"
    if chg5 < -3:
        return False, f"5m momentum rolled over ({chg5:.1f}%)"
    buys, sells = f.get("buys"), f.get("sells")
    if buys is None or sells is None:
        return False, "momentum unknown (no buy/sell counts)"
    if int(buys) + int(sells) <= 0:
        return False, "no trades in the momentum window"
    ratio = int(buys) / max(1, int(sells))
    need_r = float(g.get("min_buy_sell_ratio", 1.2))
    if ratio < need_r:
        return False, f"buy/sell {ratio:.2f} < {need_r}"
    spike = m5_spike_reason(chg5, g)
    if spike:
        return False, spike
    return True, f"{n} smart-money buyers ({how}), 1h {chg:.1f}%, 5m {chg5:.1f}%, buy/sell {ratio:.2f}"


def evaluate(facts, g, mode="gmgn"):
    gates = run_gates(facts, g, mode=mode)
    failed = [x for x in gates if not x["ok"]]
    ok_e, why = entry_rule(facts, g, mode=mode)
    reasons = [x["detail"] for x in failed]
    if not ok_e:
        reasons.append(why)
    dev = (facts or {}).get("dev") or {}
    passed = not failed and ok_e
    return {
        "ok": passed,
        "decision": "enter" if passed else "reject",
        "reasons": reasons,
        "gates": gates,
        "gates_ok": not failed,
        "entry": why,
        "entry_ok": ok_e,
        "mode": "nokey" if is_nokey(mode) else "gmgn",
        "score": display_score(facts or {}, dev),
        "score_note": "display only; a score never overrides a failed gate",
        "dev_rug_rate": dev.get("rug_rate"),
        "dev_alive": dev.get("alive"),
        "dev_analyzed": dev.get("analyzed"),
    }

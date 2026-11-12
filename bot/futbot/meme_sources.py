"""Normalize GMGN, DexScreener and RugCheck payloads into one facts dict.

Market fields (price, liquidity, volume, age, 1h/5m change, buy/sell counts) may come
from DexScreener when GMGN is absent. Security fields stay unknown unless a security
payload actually contains them. Nothing here calls a trading endpoint.

No-key discovery uses the public DexScreener GETs only: latest and top boosts,
latest token profiles (new listings; pair age comes from pairCreatedAt), and
trending metas. RugCheck reports are cached per mint. The 60/min DexScreener
routes and RugCheck are paced. No key is sent.
"""
import logging, re, threading, time
from . import meme_rules as R
from .gmgn_http import GmgnClient, GmgnError

log = logging.getLogger("meme")

DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_BOOSTS_TOP = "https://api.dexscreener.com/token-boosts/top/v1"
DEX_METAS = "https://api.dexscreener.com/metas/trending/v1"
DEX_META = "https://api.dexscreener.com/metas/meta/v1/{slug}"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"
RUG = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
SPL_LEGACY = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
# Official docs: profiles, boosts, and metas are 60 requests/minute. Pair lookups are higher.
DEX_SLOW_MARKERS = ("/token-profiles/", "/token-boosts/", "/community-takeovers/", "/ads/", "/orders/", "/metas/")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
# LP held by the incinerator or the system program is burned, not a wallet.
_BURN_MARKERS = ("1nc1nerator11111111111111111111111111111111", "11111111111111111111111111111111")
_MISS = object()


def _rows(data):
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for k in ("rank", "list", "tokens", "items", "data"):
        v = data.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _addr(row):
    for k in ("address", "token_address", "mint", "base_address"):
        v = row.get(k)
        if isinstance(v, str) and len(v) >= 32:
            return v
    return None


def _price_change_pct(v):
    """GMGN trending uses percent points (35 = +35%). Pass through numbers, reject garbage."""
    x = R._num(v)
    if x is None or abs(x) > 10000:
        return None
    return x


def from_dex_pair(p):
    if not isinstance(p, dict):
        return {}
    base = p.get("baseToken") or {}
    mint = base.get("address")
    if not mint:
        return {}
    pc, vol, liq = p.get("priceChange") or {}, p.get("volume") or {}, p.get("liquidity") or {}
    txns = p.get("txns") or {}
    tx = txns.get("h1") or {}
    tx5 = txns.get("m5") or {}
    created = p.get("pairCreatedAt")
    age = (time.time() - created / 1000.0) if created else None
    window, v = "h1", R._num(vol.get("h1"))
    if v is None:
        window, v = "h24", R._num(vol.get("h24"))
    src = "dexscreener"

    def _count(block, key):
        if not isinstance(block, dict) or block.get(key) is None:
            return None
        n = R._num(block.get(key))
        return None if n is None or n < 0 else int(n)

    return {
        "mint": mint, "symbol": base.get("symbol") or "?", "name": base.get("name") or "",
        "price": R._num(p.get("priceUsd")), "mcap": R._num(p.get("marketCap") or p.get("fdv")),
        "liquidity": R._num(liq.get("usd")), "volume": v, "volume_window": window if v is not None else None,
        "age_sec": age, "chg_1h": _price_change_pct(pc.get("h1")), "chg_5m": _price_change_pct(pc.get("m5")),
        "buys": _count(tx, "buys"), "sells": _count(tx, "sells"),
        "buys_5m": _count(tx5, "buys"), "sells_5m": _count(tx5, "sells"),
        "quote_ts": _payload_quote_ts(p),
        "sources": {"price": src, "mcap": src, "liquidity": src, "volume": src, "age": src, "momentum": src},
    }


def _payload_quote_ts(p):
    """Seconds, if the pair payload actually carries a quote time. None when it does not."""
    if not isinstance(p, dict):
        return None
    for k in ("priceTimestamp", "updatedAt", "quoteTimestamp", "lastTradeAt"):
        n = R._num(p.get(k))
        if n and n > 0:
            return n / 1000.0 if n > 1e12 else n
    return None


def _set_frac(facts, key, value, source):
    x = R.as_frac(value)
    if x is None:
        return
    facts[key] = x
    facts.setdefault("sources", {})[key] = source


def apply_security(facts, sec, source):
    """Overlay an explicit security object. Blank honeypot stays unknown."""
    if not isinstance(sec, dict):
        return facts
    sources = facts.setdefault("sources", {})
    if "renounced_mint" in sec:
        v = R.tri(sec.get("renounced_mint"))
        if v is not None:
            facts["mint_revoked"] = R.merge_tri(facts.get("mint_revoked"), v)
            sources["mint_revoked"] = source
    if "renounced_freeze_account" in sec or "renounced_freeze" in sec:
        raw = sec.get("renounced_freeze_account", sec.get("renounced_freeze"))
        v = R.tri(raw)
        if v is not None:
            facts["freeze_revoked"] = R.merge_tri(facts.get("freeze_revoked"), v)
            sources["freeze_revoked"] = source
    hp = sec.get("is_honeypot", sec.get("honeypot")) if ("is_honeypot" in sec or "honeypot" in sec) else None
    if "is_honeypot" in sec or "honeypot" in sec:
        v = R.tri(hp)
        if v is not None:
            # GMGN is_honeypot true means it IS a honeypot.
            facts["honeypot"] = R.merge_tri(facts.get("honeypot"), v)
            sources["honeypot"] = source
    if sec.get("sell_tax") not in (None, ""):
        _set_frac(facts, "sell_tax", sec.get("sell_tax"), source)
    if str(sec.get("burn_status") or "").lower() == "burn":
        facts["burn_ratio"] = 1.0
        sources["burn_ratio"] = source
        sources["lp"] = source
    elif sec.get("burn_ratio") not in (None, ""):
        _set_frac(facts, "burn_ratio", sec.get("burn_ratio"), source)
        sources["lp"] = source
    for src_k, dst in (("top_10_holder_rate", "top10"), ("dev_team_hold_rate", "dev_hold"),
                       ("bundler_trader_amount_rate", "bundler_rate"), ("bundler_rate", "bundler_rate"),
                       ("rug_ratio", "rug_ratio")):
        if sec.get(src_k) not in (None, ""):
            _set_frac(facts, dst, sec.get(src_k), source)
    if sec.get("sniper_count") not in (None, ""):
        n = R._num(sec.get("sniper_count"))
        if n is not None and n >= 0:
            facts["sniper_count"] = int(n)
            sources["sniper_count"] = source
    if sec.get("smart_degen_count") not in (None, ""):
        n = R._num(sec.get("smart_degen_count"))
        if n is not None:
            facts["smart_degen_count"] = int(n)
            sources["smart_degen_count"] = source
    if sec.get("renowned_count") not in (None, ""):
        n = R._num(sec.get("renowned_count"))
        if n is not None:
            facts["renowned_count"] = int(n)
    bw = sec.get("bundler_wallets")
    if bw is None and isinstance(sec.get("wallet_tags_stat"), dict):
        bw = sec["wallet_tags_stat"].get("bundler_wallets")
    if bw not in (None, ""):
        n = R._num(bw)
        if n is not None:
            facts["bundler_wallets"] = int(n)
            sources["bundler_rate"] = source
    sn = None
    if isinstance(sec.get("wallet_tags_stat"), dict):
        sn = sec["wallet_tags_stat"].get("sniper_wallets")
    if sn not in (None, "") and facts.get("sniper_count") is None:
        n = R._num(sn)
        if n is not None:
            facts["sniper_count"] = int(n)
            sources["sniper_count"] = source
    return facts


def apply_gmgn_info(facts, info):
    if not isinstance(info, dict):
        return facts
    sources = facts.setdefault("sources", {})
    if info.get("market_cap") is not None:
        mc = R._num(info.get("market_cap"))
        if mc is not None:
            facts["mcap"] = mc
            sources["mcap"] = "gmgn"
    if info.get("liquidity") is not None:
        liq = R._num(info.get("liquidity"))
        if liq is not None:
            facts["liquidity"] = liq
            sources["liquidity"] = "gmgn"
    if info.get("volume") is not None and not isinstance(info.get("volume"), dict):
        v = R._num(info.get("volume"))
        if v is not None:
            facts["volume"] = v
            facts["volume_window"] = str(info.get("volume_window") or "gmgn")
            sources["volume"] = "gmgn"
    if info.get("price_change_percent1h") is not None:
        c = _price_change_pct(info.get("price_change_percent1h"))
        if c is not None:
            facts["chg_1h"] = c
            sources["momentum"] = "gmgn"
    if info.get("price_change_percent5m") is not None:
        c = _price_change_pct(info.get("price_change_percent5m"))
        if c is not None:
            facts["chg_5m"] = c
            sources["momentum"] = "gmgn"
    if info.get("buys") is not None and info.get("sells") is not None and not isinstance(info.get("buys"), dict):
        facts["buys"] = int(R._num(info.get("buys")) or 0)
        facts["sells"] = int(R._num(info.get("sells")) or 0)
        sources["momentum"] = "gmgn"
    px = info.get("price")
    if not isinstance(px, dict) and px is not None:
        n = R._num(px)
        if n is not None:
            facts["price"] = n
            sources["price"] = "gmgn"
    if isinstance(px, dict):
        if facts.get("price") is None:
            facts["price"] = R._num(px.get("price"))
            sources["price"] = "gmgn"
        if facts.get("chg_1h") is None and px.get("price") and px.get("price_1h"):
            a, b = R._num(px.get("price")), R._num(px.get("price_1h"))
            if a and b:
                facts["chg_1h"] = (a / b - 1) * 100.0
                sources["momentum"] = "gmgn"
        if facts.get("chg_5m") is None and px.get("price") and px.get("price_5m"):
            a, b = R._num(px.get("price")), R._num(px.get("price_5m"))
            if a and b:
                facts["chg_5m"] = (a / b - 1) * 100.0
        for window, dst_b, dst_s in (("1h", "buys", "sells"),):
            if px.get(f"buys_{window}") is not None:
                facts["buys"] = int(R._num(px.get(f"buys_{window}")) or 0)
                facts["sells"] = int(R._num(px.get(f"sells_{window}")) or 0)
                sources["momentum"] = "gmgn"
        if px.get("volume_1h") is not None:
            facts["volume"] = R._num(px.get("volume_1h"))
            facts["volume_window"] = "h1"
            sources["volume"] = "gmgn"
    if info.get("symbol"):
        facts["symbol"] = info.get("symbol")
    ts = R._num(info.get("creation_timestamp") or info.get("open_timestamp"))
    if ts:
        facts["age_sec"] = max(0.0, time.time() - ts)
        sources["age"] = "gmgn"
    dev = info.get("dev") if isinstance(info.get("dev"), dict) else {}
    facts["creator"] = dev.get("creator_address") or facts.get("creator")
    exited = R.dev_exited_from_status(dev.get("creator_token_status"), dev.get("creator_token_balance"))
    facts["_dev_extra"] = {
        "creator": dev.get("creator_address") or "",
        "open_count": int(R._num(dev.get("creator_open_count")) or 0) if dev.get("creator_open_count") not in (None, "") else None,
        "exited": exited,
    }
    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    apply_security(facts, {
        "top_10_holder_rate": stat.get("top_10_holder_rate"),
        "dev_team_hold_rate": stat.get("dev_team_hold_rate"),
        "bundler_rate": stat.get("top_bundler_trader_percentage"),
        "wallet_tags_stat": info.get("wallet_tags_stat"),
    }, "gmgn")
    apply_security(facts, info, "gmgn")  # rank rows carry the security fields on the row itself
    return facts


def _is_burn_holder(h):
    if not isinstance(h, dict):
        return False
    tag = " ".join(str(h.get(k) or "") for k in ("tag", "type", "label", "name")).lower()
    if "burn" in tag:
        return True
    blob = " ".join(str(h.get(k) or "") for k in ("address", "owner"))
    return any(mark in blob for mark in _BURN_MARKERS)


def _lp_burn_frac(lp):
    """0-1 burn share, or None when the payload does not say. Does not treat a lock as a burn."""
    if not isinstance(lp, dict):
        return None
    for k in ("lpBurnedPct", "burnPct", "lpBurnPct"):
        if lp.get(k) not in (None, ""):
            return R.as_frac(lp.get(k))
    burned = lp.get("lpBurned")
    if burned is True or str(burned).lower() == "true":
        return 1.0
    bnum = R._num(burned)
    supply = R._num(lp.get("lpTotalSupply") or lp.get("tokenSupply") or lp.get("lpMaxSupply"))
    if bnum is not None and supply and supply > 0 and bnum >= 0:
        return min(1.0, bnum / supply)
    holders = lp.get("holders") or []
    if isinstance(holders, list) and holders:
        total, saw = 0.0, False
        for h in holders:
            if not _is_burn_holder(h):
                continue
            p = R._num(h.get("pct"))
            if p is None:
                continue
            saw = True
            total += p / 100.0 if p > 1.5 else p
        if saw:
            return min(1.0, total)
    return None


def _honeypot_phrase(text):
    t = (text or "").lower()
    return any(s in t for s in ("honeypot", "cannot sell", "can not sell", "can't sell", "cant sell"))


def apply_rugcheck(facts, rep):
    if not isinstance(rep, dict):
        return facts
    tok = rep.get("token") if isinstance(rep.get("token"), dict) else {}
    sources = facts.setdefault("sources", {})
    mint_src = tok if "mintAuthority" in tok else rep
    if "mintAuthority" in mint_src:
        revoked = mint_src.get("mintAuthority") in (None, "", "none")
        facts["mint_revoked"] = R.merge_tri(facts.get("mint_revoked"), revoked)
        sources["mint_revoked"] = "rugcheck"
    freeze_src = tok if "freezeAuthority" in tok else rep
    if "freezeAuthority" in freeze_src:
        revoked = freeze_src.get("freezeAuthority") in (None, "", "none")
        facts["freeze_revoked"] = R.merge_tri(facts.get("freeze_revoked"), revoked)
        sources["freeze_revoked"] = "rugcheck"
    risks = rep.get("risks")
    if isinstance(risks, list):
        facts["rug_risks_seen"] = True
        dangers = []
        blobs = []
        for r in risks:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or "")
            desc = str(r.get("description") or "")
            blobs.append(name + " " + desc)
            if str(r.get("level") or "").strip().lower() == "danger":
                dangers.append(name or "danger")
        facts["rug_dangers"] = dangers
        sources["rug_risks"] = "rugcheck"
        if any(_honeypot_phrase(b) for b in blobs):
            facts["honeypot"] = R.merge_tri(facts.get("honeypot"), True)
            sources["honeypot"] = "rugcheck"
    # No honeypot phrase is NOT a GMGN-mode pass. See HONESTY["honeypot"].
    lps, burns = [], []
    for m in rep.get("markets") or []:
        lp = (m or {}).get("lp") or {}
        if not isinstance(lp, dict):
            continue
        if lp.get("lpLockedPct") is not None:
            n = R._num(lp.get("lpLockedPct"))
            if n is not None:
                lps.append(n)
        if lp.get("lpBurned") is True or str(lp.get("lpBurned")).lower() == "true":
            lps.append(100.0)
        b = _lp_burn_frac(lp)
        if b is not None:
            burns.append(b)
    if lps:
        facts["lp_locked_pct"] = max(lps)
        sources["lp_locked_pct"] = "rugcheck"
        sources["lp"] = "rugcheck"
    # Stored aside so GMGN mode still requires its own burn_ratio / lock fields.
    # No-key gates read rug_burn_ratio when burn_ratio was not set by GMGN.
    if burns:
        facts["rug_burn_ratio"] = max(burns)
        sources["rug_burn_ratio"] = "rugcheck"
        sources["lp"] = sources.get("lp") or "rugcheck"
    holders = [h for h in (rep.get("topHolders") or []) if isinstance(h, dict)]
    if holders and facts.get("top10") is None:
        pcts = []
        for h in holders[:10]:
            if h.get("pct") is None:
                pcts = None
                break
            pcts.append(float(h["pct"]))
        if pcts:
            s = sum(pcts)
            facts["top10"] = s / 100.0 if s > 1.5 else s
            sources["top10"] = "rugcheck"
    program = str(rep.get("tokenProgram") or tok.get("tokenProgram") or "")
    fee = rep.get("transferFee") if "transferFee" in rep else tok.get("transferFee") if "transferFee" in tok else None
    if "transferFee" in rep or "transferFee" in tok:
        if isinstance(fee, dict) and fee.get("pct") is not None:
            _set_frac(facts, "sell_tax", fee.get("pct"), "rugcheck")
        elif fee is None and SPL_LEGACY in program and facts.get("sell_tax") is None:
            facts["sell_tax"] = 0.0
            sources["sell_tax"] = "rugcheck_spl_no_transfer_fee"
    score = rep.get("score_normalised")
    if score is None:
        score = rep.get("score")
    nscore = R._num(score)
    if nscore is not None:
        facts["rug_score"] = nscore
        sources["rug_score"] = "rugcheck"
    return facts


def smart_buyers_from_traders(rows, smart_set=None):
    """Distinct addresses that are smart-money AND buying (buy volume > sell volume).
    Returns (list or None, note). None means the payload was missing, not 'zero wallets'."""
    if rows is None:
        return None, "traders not fetched"
    smart_set = set(smart_set or [])
    found = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        addr = t.get("address") or t.get("wallet_address") or t.get("maker")
        tags = set()
        for k in ("maker_token_tags", "tags", "tag"):
            v = t.get(k)
            if isinstance(v, list):
                tags.update(str(x).lower() for x in v)
            elif isinstance(v, str):
                tags.add(v.lower())
        is_smart = bool(tags & {"smart_degen", "smart", "smart_money"}) or (addr and addr in smart_set)
        buy = t.get("buy_volume_cur", t.get("buy_volume"))
        sell = t.get("sell_volume_cur", t.get("sell_volume"))
        if buy is None and sell is None:
            continue  # cannot tell they are buying
        if not is_smart or not addr:
            continue
        if (R._num(buy) or 0) > (R._num(sell) or 0):
            found.append(addr)
    return list(dict.fromkeys(found)), "gmgn_traders"


def blank(mint, symbol="?"):
    return {"mint": mint, "symbol": symbol, "conflicts": [], "sources": {}}


def note_conflicts(facts):
    conflicts = []
    for k in ("mint_revoked", "freeze_revoked", "honeypot"):
        if facts.get(k) == "conflict":
            conflicts.append(k)
    facts["conflicts"] = conflicts
    return facts


def pace_bucket(url):
    """DexScreener profiles/boosts/metas are ~60/min. Token lookups are higher. RugCheck is its own bucket."""
    u = url or ""
    if "rugcheck.xyz" in u:
        return "rug"
    if any(m in u for m in DEX_SLOW_MARKERS):
        return "slow"
    return "fast"


class MinuteBudget:
    """Sleep so calls stay under `per_min` in any rolling 60 seconds."""

    def __init__(self, per_min, clock=None, sleep=None):
        self.per_min = max(1, int(per_min))
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.hits = []
        self._lock = threading.Lock()

    def take(self):
        while True:
            with self._lock:
                now = self.clock()
                self.hits = [t for t in self.hits if now - t < 60.0]
                if len(self.hits) < self.per_min:
                    self.hits.append(self.clock())
                    return
                wait = 60.0 - (now - self.hits[0]) + 0.02
            self.sleep(max(0.0, wait))


class MinInterval:
    """Minimum gap between calls. The first call does not sleep."""

    def __init__(self, seconds, clock=None, sleep=None):
        self.seconds = max(0.0, float(seconds))
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last = None
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            now = self.clock()
            if self.last is not None and self.seconds > 0:
                gap = self.seconds - (now - self.last)
                if gap > 0:
                    self.sleep(gap)
            self.last = self.clock()


def _interleave(feeds, limit):
    """Round-robin unique mints so boosts, new profiles, and trending all fit in `limit`."""
    out, seen = [], set()
    i = 0
    while len(out) < limit:
        progressed = False
        for lst in feeds:
            if i < len(lst):
                progressed = True
                m = lst[i]
                if m not in seen:
                    seen.add(m)
                    out.append(m)
                    if len(out) >= limit:
                        break
        if not progressed:
            break
        i += 1
    return out


class MarketData:
    """Sync HTTP. Tests inject `get`. No-key mode is selected when the GMGN client is disabled."""

    def __init__(self, cfg=None, gmgn=None, get=None, clock=None):
        self.cfg = cfg or {}
        self.g = (self.cfg.get("strategy") or {}).get("gmgn") or {}
        self.gmgn = gmgn if gmgn is not None else GmgnClient()
        self._get = get  # (url) -> (status, obj) or raises
        self._clock = clock or time.monotonic
        self.mode = "gmgn" if self.gmgn.enabled else "nokey"
        self.status = {"mode": self.mode, "gmgn": "no key", "dexscreener": "idle", "rugcheck": "idle"}
        if self.gmgn.refused_demo:
            self.status["gmgn"] = "demo key refused"
        elif self.gmgn.enabled:
            self.status["gmgn"] = "key present"
        self._budgets = {
            "slow": MinuteBudget(int(self.g.get("dex_slow_rpm", 50)), clock=self._clock),
            "fast": MinuteBudget(int(self.g.get("dex_fast_rpm", 240)), clock=self._clock),
            "rug": MinuteBudget(int(self.g.get("rugcheck_rpm", 50)), clock=self._clock),
        }
        self._rug_gap = MinInterval(float(self.g.get("rugcheck_min_interval_sec", 1.0)), clock=self._clock)
        self._rug_cache = {}
        self._hints = {}
        self.rug_fetches = 0
        self._lock = threading.Lock()
        why = "GMGN_API_KEY present" if self.mode == "gmgn" else (
            "demo key refused" if self.gmgn.refused_demo else "GMGN_API_KEY missing")
        log.info("meme data mode: %s (%s)", "gmgn" if self.mode == "gmgn" else "no-key", why)

    def mode_label(self):
        if self.mode == "gmgn":
            return "gmgn"
        if getattr(self.gmgn, "refused_demo", False):
            return "no-key (demo key refused)"
        return "no-key (GMGN_API_KEY missing)"

    def _http(self, url):
        bucket = pace_bucket(url)
        self._budgets[bucket].take()
        if bucket == "rug":
            self._rug_gap.take()
        if self._get:
            return self._get(url)
        import httpx
        r = httpx.get(url, headers={"User-Agent": "dexblocker-paper/1.0"}, timeout=12.0)
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status_code, body

    def discover_dex(self, limit):
        mints = []
        for url in (DEX_PROFILES, DEX_BOOSTS):
            try:
                status, body = self._http(url)
            except Exception:
                self.status["dexscreener"] = "down"
                continue
            if status != 200 or not isinstance(body, list):
                continue
            for row in body:
                if (row or {}).get("chainId") in ("solana", "sol") and row.get("tokenAddress"):
                    mints.append(row["tokenAddress"])
        self.status["dexscreener"] = f"{len(mints)} discovered" if mints else self.status["dexscreener"]
        # preserve order, unique
        out = list(dict.fromkeys(mints))
        return out[:limit]

    def _feed_mints(self, url):
        try:
            status, body = self._http(url)
        except Exception:
            self.status["dexscreener"] = "down"
            return []
        if status != 200 or not isinstance(body, list):
            return []
        out = []
        for row in body:
            if isinstance(row, dict) and row.get("chainId") in ("solana", "sol") and row.get("tokenAddress"):
                out.append(row["tokenAddress"])
        return out

    def _trending_pairs(self, max_metas):
        """Solana pairs from the public trending-metas feed. Official API; no key."""
        try:
            status, body = self._http(DEX_METAS)
        except Exception:
            return [], {}
        if status != 200 or not isinstance(body, list):
            return [], {}
        mints, hints = [], {}
        fetched = 0
        for meta in body:
            if fetched >= max_metas:
                break
            slug = str((meta or {}).get("slug") or "")
            if not _SLUG.fullmatch(slug):
                continue
            fetched += 1
            try:
                st, page = self._http(DEX_META.format(slug=slug))
            except Exception:
                continue
            if st != 200 or not isinstance(page, dict):
                continue
            for p in page.get("pairs") or []:
                if not isinstance(p, dict) or p.get("chainId") not in ("solana", "sol"):
                    continue
                mint = ((p.get("baseToken") or {}).get("address"))
                if not isinstance(mint, str) or len(mint) < 32:
                    continue
                mints.append(mint)
                prev = hints.get(mint)
                liq = R._num(((p.get("liquidity") or {}).get("usd"))) or 0
                prev_liq = R._num((((prev or {}).get("liquidity") or {}).get("usd"))) or 0
                if prev is None or liq >= prev_liq:
                    hints[mint] = p
        return mints, hints

    def discover_nokey(self, limit):
        """Boosts, latest profiles (new listings), and trending metas. Solana only."""
        limit = max(1, int(limit))
        feeds, counts = [], {}
        for name, url in (("boosts", DEX_BOOSTS), ("new_profiles", DEX_PROFILES), ("boosts_top", DEX_BOOSTS_TOP)):
            rows = self._feed_mints(url)
            counts[name] = len(rows)
            feeds.append(rows)
        trending, hints = self._trending_pairs(max(0, int(self.g.get("trending_metas", 2))))
        counts["trending"] = len(trending)
        feeds.append(trending)
        self._hints.update(hints)
        out = _interleave(feeds, limit)
        self.status["dexscreener"] = "no-key " + " ".join(f"{k}={v}" for k, v in counts.items())
        self.status["discover"] = counts
        return out

    def dex_pairs(self, mints):
        if not mints:
            return {}
        url = DEX_TOKENS + ",".join(mints[:30])
        best = {}
        try:
            status, body = self._http(url)
        except Exception:
            self.status["dexscreener"] = "down"
            status, body = None, None
        if status == 200:
            rows = body if isinstance(body, list) else (body or {}).get("pairs") or []
            for p in rows:
                mint = ((p or {}).get("baseToken") or {}).get("address")
                if not mint:
                    continue
                liq = R._num(((p.get("liquidity") or {}).get("usd"))) or 0
                if mint not in best or liq > best[mint][0]:
                    best[mint] = (liq, p)
        elif status is not None:
            self.status["dexscreener"] = f"HTTP {status}"
        out = {m: p for m, (_, p) in best.items()}
        for m in mints:
            if m not in out and m in self._hints:
                out[m] = self._hints[m]
        if out:
            self.status["dexscreener"] = f"{len(out)} priced"
        return out

    def gmgn_rank(self, limit):
        if not self.gmgn.enabled:
            return []
        try:
            data = self.gmgn.get("/v1/market/rank", {"chain": "sol", "interval": "1h", "limit": int(limit)})
        except GmgnError:
            try:
                data = self.gmgn.get("/v1/market/rank", {"chain": "sol", "interval": "1h"})
            except GmgnError as e:
                self.status["gmgn"] = f"rank failed {str(e)[:80]}"
                return []
        rows = _rows(data)
        self.status["gmgn"] = f"rank {len(rows)}"
        return rows

    def gmgn_enrich(self, mint):
        """info, security, pool, traders, created-tokens. Any miss leaves fields unknown."""
        out = {"info": None, "security": None, "pool": None, "traders": None, "created": None, "smart": None}
        if not self.gmgn.enabled:
            return out
        q = {"chain": "sol", "address": mint}
        for key, path, query in (
            ("info", "/v1/token/info", q),
            ("security", "/v1/token/security", q),
            ("pool", "/v1/token/pool_info", q),
            ("traders", "/v1/market/token_top_traders", {**q, "limit": 30}),
        ):
            try:
                out[key] = self.gmgn.get(path, query)
            except GmgnError:
                out[key] = None
        return out

    def gmgn_smart_set(self):
        if not self.gmgn.enabled:
            return set()
        try:
            data = self.gmgn.get("/v1/user/smartmoney", {"chain": "sol", "limit": 80})
        except GmgnError:
            return set()
        addrs = set()
        for row in _rows(data):
            a = row.get("address") or row.get("wallet_address") or row.get("wallet")
            if isinstance(a, str):
                addrs.add(a)
        return addrs

    def gmgn_created(self, wallet):
        if not self.gmgn.enabled or not wallet:
            return None
        try:
            return self.gmgn.get("/v1/user/created_tokens", {"chain": "sol", "wallet_address": wallet})
        except GmgnError:
            return None

    def _cache_get(self, mint):
        row = self._rug_cache.get(mint)
        if not row:
            return _MISS
        exp, body = row
        if self._clock() >= exp:
            return _MISS
        return body

    def rug_cached(self, mint):
        with self._lock:
            return self._cache_get(mint) is not _MISS

    def _remember_rug(self, mint, body, fail):
        key = "rugcheck_fail_cache_sec" if fail else "rugcheck_cache_sec"
        ttl = float(self.g.get(key, 45 if fail else 600))
        with self._lock:
            self._rug_cache[mint] = (self._clock() + max(0.0, ttl), body)

    def rugcheck(self, mint):
        """Cached per mint. A cached miss is not a pass; callers still fail closed."""
        if not self.g.get("rugcheck", True):
            self.status["rugcheck"] = "off"
            return None
        if not mint:
            return None
        with self._lock:
            hit = self._cache_get(mint)
            if hit is not _MISS:
                self.status["rugcheck"] = "cache"
                return hit
        try:
            status, body = self._http(RUG.format(mint=mint))
        except Exception:
            self.status["rugcheck"] = "down"
            self._remember_rug(mint, None, fail=True)
            return None
        if status != 200 or not isinstance(body, dict):
            self.status["rugcheck"] = f"HTTP {status}"
            self._remember_rug(mint, None, fail=True)
            return None
        self.rug_fetches += 1
        self._remember_rug(mint, body, fail=False)
        self.status["rugcheck"] = "ok"
        return body


def assemble(mint, dex_pair=None, gmgn_row=None, info=None, security=None, pool=None, traders=None,
             smart_set=None, created=None, rug=None):
    facts = blank(mint)
    if dex_pair:
        facts.update({k: v for k, v in from_dex_pair(dex_pair).items() if k != "sources"})
        facts["sources"] = dict(from_dex_pair(dex_pair).get("sources") or {})
    if isinstance(gmgn_row, dict):
        apply_gmgn_info(facts, gmgn_row)
    if isinstance(info, dict):
        apply_gmgn_info(facts, info)
    if isinstance(security, dict):
        apply_security(facts, security, "gmgn")
    if isinstance(pool, dict):
        if facts.get("liquidity") is None and pool.get("liquidity") is not None:
            facts["liquidity"] = R._num(pool.get("liquidity"))
            facts["sources"]["liquidity"] = "gmgn"
        if str(pool.get("burn_status") or "").lower() == "burn":
            facts["burn_ratio"] = 1.0
            facts["sources"]["lp"] = "gmgn"
    buyers, how = smart_buyers_from_traders(_rows(traders) if isinstance(traders, (dict, list)) else traders, smart_set)
    if buyers is not None:
        facts["smart_buyers"] = buyers
        facts["sources"]["smart_money"] = how
    extra = facts.pop("_dev_extra", None) or {}
    token_rows = None
    if isinstance(created, dict):
        token_rows = created.get("tokens") or created.get("list")
        if extra.get("inner_count") is None and created.get("inner_count") is not None:
            extra["inner_count"] = int(R._num(created.get("inner_count")) or 0)
    elif isinstance(created, list):
        token_rows = created
    if token_rows is not None:
        facts["dev"] = R.dev_from_tokens(token_rows, extra)
    elif extra.get("creator") or extra.get("open_count") is not None:
        facts["dev"] = R.dev_from_tokens([], extra)  # rug_rate stays None
    if rug:
        apply_rugcheck(facts, rug)
    if not facts.get("symbol"):
        facts["symbol"] = mint[:4]
    return note_conflicts(facts)

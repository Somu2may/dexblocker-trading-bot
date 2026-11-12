"""No-key gmgn_meme: DexScreener + RugCheck when GMGN_API_KEY is absent.

GMGN mode stays fail-closed on the same fixtures. No signing, no swap routes.
"""
import logging, os, tempfile, time
from futbot import config, meme_rules as R
from futbot.meme_paper import MemeBook
from futbot.meme_run import Runner
from futbot.meme_sources import (
    DEX_BOOSTS, DEX_METAS, DEX_TOKENS, MinuteBudget, RUG, _interleave,
    apply_rugcheck, from_dex_pair, pace_bucket,
)
from futbot.meme_sources import MarketData
from futbot.store import Store

G = config.load()["strategy"]["gmgn"]
NOW = 1_700_000_000.0
MINT = "A" * 43
MINT2 = "B" * 43


def nokey(**over):
    f = {
        "mint": MINT, "symbol": "CAT", "price": 0.002, "mcap": 150_000,
        "liquidity": 40_000, "volume": 25_000, "volume_window": "h1", "age_sec": 3600,
        "chg_1h": 18.0, "chg_5m": 4.0, "buys": 40, "sells": 20,
        "buys_5m": 10, "sells_5m": 4,
        "mint_revoked": True, "freeze_revoked": True, "burn_ratio": None, "lp_locked_pct": 90,
        "rug_burn_ratio": None, "top10": 0.22, "honeypot": None, "sell_tax": 0.0,
        "rug_risks_seen": True, "rug_dangers": [],
        "sources": {}, "conflicts": [],
    }
    f.update(over)
    return f


class _Mem:
    def __init__(self):
        self.d = {}
    def get(self, k, default=None):
        return self.d.get(k, default)
    def put(self, k, v):
        self.d[k] = v


class _Off:
    enabled = False
    refused_demo = False


class _On:
    def __init__(self):
        self.enabled = True
        self.refused_demo = False
        self.calls = []
    def get(self, path, query=None):
        self.calls.append(path)
        if path == "/v1/market/rank":
            return {"rank": [{"address": MINT}]}
        return {}


def test_nokey_passes_without_gmgn_only_fields_and_gmgn_mode_still_rejects():
    f = nokey()
    d = R.evaluate(f, G, mode="nokey")
    assert d["ok"] and d["decision"] == "enter" and d["gates_ok"] and d["mode"] == "nokey"
    assert "no-key flow" in d["entry"]
    skipped = [x["code"] for x in d["gates"] if x.get("skipped")]
    assert skipped == ["dev_history", "dev_exited", "dev_holding", "bundled", "snipers"]
    # Same payload is not a GMGN pass: no explicit honeypot "no", no dev list, no smart money.
    g = R.evaluate(f, G)
    assert g["ok"] is False and g["mode"] == "gmgn"
    assert any("honeypot" in r.lower() for r in g["reasons"])
    assert any("smart money" in r.lower() for r in g["reasons"])


def test_nokey_missing_remaining_gate_rejects_and_danger_and_honeypot_flags():
    for key in ("mint_revoked", "freeze_revoked", "top10", "sell_tax", "liquidity", "volume", "age_sec", "mcap"):
        f = nokey()
        f[key] = None
        if key == "mint_revoked":
            f["mint_revoked"] = None
        d = R.evaluate(f, G, mode="nokey")
        assert d["ok"] is False and d["gates_ok"] is False, key
    f = nokey(lp_locked_pct=None, burn_ratio=None, rug_burn_ratio=None)
    assert any("LP" in r or "burn" in r.lower() for r in R.evaluate(f, G, mode="nokey")["reasons"])
    # Burn percent is a no-key measurement. GMGN mode does not treat rug_burn_ratio as its own burn field.
    burned = nokey(lp_locked_pct=10, burn_ratio=None, rug_burn_ratio=0.96)
    assert R.evaluate(burned, G, mode="nokey")["gates_ok"]
    assert any("LP" in r or "burn" in r.lower() for r in R.evaluate(burned, G)["reasons"])
    locked = nokey(lp_locked_pct=80, burn_ratio=None, rug_burn_ratio=None)
    assert R.evaluate(locked, G, mode="nokey")["gates_ok"]
    short = nokey(lp_locked_pct=79, burn_ratio=None, rug_burn_ratio=0.90)
    assert not R.evaluate(short, G, mode="nokey")["gates_ok"]
    danger = nokey(rug_dangers=["Mint Authority still enabled"])
    reasons = R.evaluate(danger, G, mode="nokey")["reasons"]
    assert any("danger" in r.lower() for r in reasons)
    pot = nokey(honeypot=True)
    assert any("honeypot" in r.lower() for r in R.evaluate(pot, G, mode="nokey")["reasons"])
    unseen = nokey(rug_risks_seen=False, rug_dangers=None, honeypot=False)
    ru = R.evaluate(unseen, G, mode="nokey")["reasons"]
    assert any("risk list" in r.lower() for r in ru)
    # Explicit GMGN "no" still does not waive a missing RugCheck list in no-key mode.
    assert not R.evaluate(unseen, G, mode="nokey")["gates_ok"]


def test_nokey_entry_replaces_smart_money():
    assert R.evaluate(nokey(smart_buyers=None), G, mode="nokey")["ok"]
    assert R.evaluate(nokey(buys=12, sells=10), G, mode="nokey")["ok"]  # 1.2, above the 1.1 bar
    low = nokey(buys=10, sells=10)  # 1.0, under 1.1
    assert any("buy/sell" in r.lower() for r in R.evaluate(low, G, mode="nokey")["reasons"])
    assert R.evaluate(nokey(buys=11, sells=10), G, mode="nokey")["ok"]
    assert R.evaluate(nokey(buys_5m=5, sells_5m=5), G, mode="nokey")["ok"]  # equal 5m flow is allowed
    below5 = nokey(buys_5m=4, sells_5m=5)
    assert any("below sells" in r.lower() for r in R.evaluate(below5, G, mode="nokey")["reasons"])
    assert any("5m buy/sell unknown" in r.lower() for r in R.evaluate(nokey(buys_5m=None), G, mode="nokey")["reasons"])
    assert any("momentum" in r.lower() for r in R.evaluate(nokey(chg_1h=2.9), G, mode="nokey")["reasons"])
    assert R.evaluate(nokey(chg_1h=3.0), G, mode="nokey")["ok"]
    assert R.evaluate(nokey(chg_1h=100.0), G, mode="nokey")["ok"]
    assert any("chase" in r.lower() for r in R.evaluate(nokey(chg_1h=100.1), G, mode="nokey")["reasons"])
    assert any("rolled over" in r.lower() for r in R.evaluate(nokey(chg_5m=-3.1), G, mode="nokey")["reasons"])
    assert R.evaluate(nokey(chg_5m=-3.0), G, mode="nokey")["ok"]
    # Gates can pass while the flow entry does not.
    weak = nokey(buys_5m=1, sells_5m=4)
    d = R.evaluate(weak, G, mode="nokey")
    assert d["gates_ok"] and not d["ok"]


def test_moderate_gate_defaults_and_unchanged_risk():
    assert G["min_liquidity_usd"] == 8000 and G["min_volume_usd"] == 6000
    assert G["nokey_min_buy_sell_ratio"] == 1.1
    assert G["max_top10_pct"] == 50
    assert G["min_age_sec"] == 120 and G["max_age_sec"] == 72 * 3600
    assert G["min_momentum_pct"] == 3 and G["max_chase_pct"] == 100
    assert G["gates_rev"] == 2 and config.load()["defaults_rev"] == 3 and config.load()["mode"] == "paper"
    # Mint/freeze, LP, danger, size, and stop stay as they were.
    assert G["min_burn_pct"] == 95 and G["min_lp_locked_pct"] == 80
    assert G["min_buy_sell_ratio"] == 1.2
    assert G["position_size_pct"] == 1.5 and G["stop_loss_pct"] == 10
    assert R.evaluate(nokey(liquidity=8000, volume=6000, top10=0.50, age_sec=120), G, mode="nokey")["gates_ok"]
    assert R.evaluate(nokey(age_sec=72 * 3600), G, mode="nokey")["gates_ok"]
    for bad in (dict(liquidity=7999), dict(volume=5999), dict(top10=0.51), dict(age_sec=119), dict(age_sec=72 * 3600 + 1)):
        assert not R.evaluate(nokey(**bad), G, mode="nokey")["gates_ok"]


def test_rugcheck_maps_burn_dangers_and_does_not_invent_a_honeypot_pass():
    facts = {"sources": {}}
    apply_rugcheck(facts, {
        "risks": [{"name": "Fee config", "description": "cannot sell these tokens", "level": "warn"}],
        "markets": [{"lp": {"lpLockedPct": 10, "lpBurned": 960, "lpTotalSupply": 1000}}],
    })
    assert facts["honeypot"] is True
    assert facts["rug_dangers"] == []
    assert facts["rug_risks_seen"] is True
    assert facts["rug_burn_ratio"] == pytest_approx(0.96)
    assert "burn_ratio" not in facts
    facts = {"sources": {}}
    apply_rugcheck(facts, {"risks": [{"name": "Mint Authority still enabled", "level": "Danger"}]})
    assert facts["rug_dangers"] == ["Mint Authority still enabled"]
    assert facts.get("honeypot") is None
    facts = {"sources": {}}
    apply_rugcheck(facts, {"markets": [{"lp": {"holders": [
        {"address": "1nc1nerator11111111111111111111111111111111", "pct": 97},
    ]}}]})
    assert facts["rug_burn_ratio"] == pytest_approx(0.97)
    # The existing "no phrase is not a pass" case still leaves honeypot unset.
    facts = {"sources": {}}
    apply_rugcheck(facts, {"token": {"mintAuthority": None, "freezeAuthority": None},
                           "risks": [], "markets": [{"lp": {"lpLockedPct": 99}}]})
    assert facts.get("honeypot") is None and facts["rug_risks_seen"] is True and facts["rug_dangers"] == []
    assert facts["lp_locked_pct"] == 99


def test_dex_pair_reads_m5_flow_and_fdv():
    now = time.time()
    p = from_dex_pair({
        "baseToken": {"address": MINT, "symbol": "CAT"},
        "priceUsd": "0.01", "fdv": 12000,
        "liquidity": {"usd": 20000}, "volume": {"h1": 15000},
        "priceChange": {"h1": 12, "m5": 1},
        "txns": {"h1": {"buys": 13, "sells": 10}, "m5": {"buys": 4, "sells": 1}},
        "pairCreatedAt": (now - 600) * 1000,
    })
    assert p["mcap"] == 12000 and p["buys"] == 13 and p["sells"] == 10
    assert p["buys_5m"] == 4 and p["sells_5m"] == 1
    assert R.market_ready(p, G)


def test_rate_budget_and_rugcheck_cache(caplog):
    assert pace_bucket(DEX_BOOSTS) == "slow"
    assert pace_bucket(DEX_METAS) == "slow"
    assert pace_bucket(DEX_TOKENS + "abc") == "fast"
    assert pace_bucket(RUG.format(mint="m")) == "rug"
    t = {"n": 0.0}
    slept = []

    def clock():
        return t["n"]

    def sleep(s):
        slept.append(s)
        t["n"] += s

    budget = MinuteBudget(2, clock=clock, sleep=sleep)
    budget.take(); budget.take(); budget.take()
    assert slept and slept[0] >= 60
    assert _interleave([["a", "b"], ["c"], ["a", "d"]], 4) == ["a", "c", "b", "d"]

    caplog.set_level(logging.INFO, logger="meme")
    calls = []

    def get(url):
        calls.append(url)
        return 200, {"risks": [], "token": {"mintAuthority": None, "freezeAuthority": None}}

    cfg = config.load()
    cfg["strategy"]["gmgn"]["rugcheck_min_interval_sec"] = 0
    data = MarketData(cfg, gmgn=_Off(), get=get)
    assert data.mode == "nokey"
    assert "no-key" in caplog.text and "GMGN_API_KEY missing" in caplog.text
    assert "GMGN_API_KEY=" not in caplog.text
    assert data.rugcheck(MINT)["risks"] == []
    assert data.rugcheck(MINT)["risks"] == []
    assert data.rug_fetches == 1
    assert sum(1 for u in calls if "rugcheck" in u) == 1
    gap = []
    data._rug_gap.seconds = 1.0
    data._rug_gap.sleep = lambda s: gap.append(s)
    data.rugcheck(MINT2)
    assert gap and gap[0] > 0
    on = _On()
    keyed = MarketData(cfg, gmgn=on, get=get)
    assert keyed.mode == "gmgn" and keyed.mode_label() == "gmgn"


def _pair(mint, liq=40000, vol=25000, mcap=150000, age_s=3600, chg=18, chg5=4, buys=40, sells=20, b5=10, s5=4):
    return {
        "chainId": "solana",
        "baseToken": {"address": mint, "symbol": "CAT" if mint == MINT else "DOG", "name": "Cat"},
        "priceUsd": "0.002", "marketCap": mcap, "fdv": mcap,
        "liquidity": {"usd": liq}, "volume": {"h1": vol},
        "priceChange": {"h1": chg, "m5": chg5},
        "txns": {"h1": {"buys": buys, "sells": sells}, "m5": {"buys": b5, "sells": s5}},
        "pairCreatedAt": (time.time() - age_s) * 1000,
    }


def _rug():
    return {
        "token": {"mintAuthority": None, "freezeAuthority": None},
        "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "transferFee": None,
        "risks": [],
        "topHolders": [{"pct": 2.0} for _ in range(10)],
        "markets": [{"lp": {"lpLockedPct": 100}}],
    }


def test_scan_nokey_enters_on_free_sources_and_caches_rugcheck():
    calls = []

    def get(url):
        calls.append(url)
        if url.endswith("/token-boosts/latest/v1"):
            return 200, [{"chainId": "solana", "tokenAddress": MINT},
                         {"chainId": "ethereum", "tokenAddress": "0xabc"}]
        if url.endswith("/token-profiles/latest/v1"):
            return 200, [{"chainId": "solana", "tokenAddress": MINT2}]
        if url.endswith("/token-boosts/top/v1"):
            return 200, []
        if url.endswith("/metas/trending/v1"):
            return 200, [{"slug": "cat", "name": "Cat"}, {"slug": "not a slug!", "name": "no"}]
        if "/metas/meta/" in url:
            return 200, {"pairs": [_pair(MINT, liq=1000)]}  # too thin; the boosts row is the one that can pass
        if "/tokens/v1/solana/" in url:
            ids = url.rstrip("/").split("/")[-1].split(",")
            rows = []
            for m in ids:
                if m == MINT:
                    rows.append(_pair(MINT))
                elif m == MINT2:
                    rows.append(_pair(MINT2, liq=100))  # fails the liquidity gate before RugCheck
            return 200, rows
        if "rugcheck.xyz" in url:
            return 200, _rug()
        return 404, None

    cfg = config.load()
    g = cfg["strategy"]["gmgn"]
    g["rugcheck_min_interval_sec"] = 0
    g["discover_limit"] = 4
    g["trending_metas"] = 2
    data = MarketData(cfg, gmgn=_Off(), get=get)
    store = Store(os.path.join(tempfile.mkdtemp(), "dry.db"))

    class E:
        running = True
        def __init__(self):
            self.cfg = cfg
            self.store = store
            self.data_dir = None
        def _event(self, e):
            pass
        def notify(self, m):
            pass

    run = Runner(E(), data)
    rows = run.scan_once(limit=4)
    by = {r["mint"]: r for r in rows}
    assert by[MINT]["decision"] == "enter" and by[MINT]["gates_ok"] and by[MINT]["mode"] == "nokey"
    assert by[MINT2]["decision"] == "reject" and not by[MINT2]["gates_ok"]
    assert MINT not in run.book.positions  # first pass only confirms
    assert any("confirmation 1/2" in r for r in by[MINT]["reasons"])
    run._confirm_at[MINT] = time.time() - 31
    rows = run.scan_once(limit=4)
    assert MINT in run.book.positions
    pos = run.book.positions[MINT]
    assert "liq=" in (pos.get("entry_metrics") or "") and "top10=" in pos["entry_metrics"]
    assert pos["notional0"] == pytest_approx(1000 * 1.5 / 100, rel=0.02)
    assert pos["stop"] < pos["entry"]
    assert abs(pos["stop"] / pos["entry"] - 0.90) < 0.02
    rug_calls = [u for u in calls if "rugcheck.xyz" in u]
    assert rug_calls == [RUG.format(mint=MINT)]
    assert not any("openapi.gmgn.ai" in u or "/v1/trade" in u for u in calls)
    assert any("/token-boosts/latest" in u for u in calls)
    assert any("/token-profiles/latest" in u for u in calls)
    assert any("/metas/trending" in u for u in calls)
    assert any("/metas/meta/v1/cat" in u for u in calls)
    n1 = data.rug_fetches
    run.scan_once(limit=4)
    assert data.rug_fetches == n1  # second pass uses the per-mint cache
    # Risk model is the existing paper book: a stop print still closes.
    run.book.mark(MINT, pos["stop"] * 0.99, 40_000, NOW)
    assert MINT not in run.book.positions and run.book.closed[-1]["pnl"] < 0


def test_scan_with_key_keeps_gmgn_rank_and_does_not_use_nokey_discovery():
    calls = []

    def get(url):
        calls.append(url)
        if "rugcheck" in url:
            return 200, {"token": {"mintAuthority": "still"}, "risks": []}
        if "/tokens/v1/" in url:
            return 200, []
        return 200, []

    cfg = config.load()
    cfg["strategy"]["gmgn"]["discover_limit"] = 1
    cfg["strategy"]["gmgn"]["rugcheck_min_interval_sec"] = 0
    on = _On()
    data = MarketData(cfg, gmgn=on, get=get)
    store = Store(os.path.join(tempfile.mkdtemp(), "dry.db"))

    class E:
        running = False
        def __init__(self):
            self.cfg = cfg
            self.store = store
            self.data_dir = None
        def _event(self, e):
            pass
        def notify(self, m):
            pass

    rows = Runner(E(), data).scan_once(limit=1)
    assert rows and rows[0]["mode"] == "gmgn" and rows[0]["decision"] == "reject"
    assert "/v1/market/rank" in on.calls
    assert not any("/metas/" in u for u in calls)
    assert any("dev history" in r.lower() or "honeypot" in r.lower() or "unknown" in r.lower() for r in rows[0]["reasons"])


def test_reentry_cooldown_quote_spike_and_major_ticker():
    assert R.is_major_impersonation("BTC", "") and R.is_major_impersonation("$eth", "")
    assert R.is_major_impersonation("ETHSOL", "") and R.is_major_impersonation("CAT", "Bitcoin")
    assert not R.is_major_impersonation("CAT", "Cat") and not R.is_major_impersonation("SOLDOG", "Slingoor")
    blocked = nokey(symbol="BTC", name="Bitcoin")
    assert any("major" in r.lower() for r in R.evaluate(blocked, G, mode="nokey")["reasons"])
    assert any("spike" in r.lower() for r in R.evaluate(nokey(chg_5m=26), G, mode="nokey")["reasons"])
    assert R.evaluate(nokey(chg_5m=25), G, mode="nokey")["ok"]
    assert R.quote_entry_block({"quote_ts": NOW - 5, "chg_5m": 1}, G, NOW)[0]
    assert not R.quote_entry_block({"quote_ts": NOW - 31}, G, NOW)[0]
    assert not R.quote_entry_block({}, G, NOW)[0]
    seen = {}
    first = {"mint": MINT, "price": 1.0, "chg_5m": 1.0, "buys": 4, "sells": 2}
    R.stamp_quote(first, 1_000, seen)
    again = dict(first)
    R.stamp_quote(again, 1_040, seen)
    assert again["quote_ts"] == 1_000
    assert not R.quote_entry_block(again, G, 1_040)[0]
    moved = dict(first, price=1.2)
    moved.pop("quote_ts", None)
    R.stamp_quote(moved, 1_040, seen)
    assert moved["quote_ts"] == 1_040
    b = MemeBook(config.load(), _Mem(), on_event=lambda e: None)
    pos, msg = b.open_pos(nokey(), "flow", NOW)
    assert msg == "opened" and "liq=" in pos["entry_metrics"]
    assert not b.can_open(pos["mint"], NOW)[0] and "already" in b.can_open(pos["mint"], NOW)[1]
    b.mark(pos["mint"], pos["stop"] * 0.8, 40_000, NOW + 5)
    closed = b.closed[-1]
    assert closed["exit"] < pos["stop"] and "gap" in closed["reason"] and "liq=" in closed["reason"]
    assert not b.can_open(pos["mint"], NOW + 60)[0] and "cooldown" in b.can_open(pos["mint"], NOW + 60)[1]
    assert b.can_open(pos["mint"], NOW + 5 + 86400)[0]
    b2 = MemeBook(config.load(), _Mem(), on_event=lambda e: None)
    p2, _ = b2.open_pos(nokey(), "flow", NOW)
    b2.mark(p2["mint"], p2["entry"] * 0.99, 40_000, NOW + 26 * 60)
    assert "time stop below entry" in b2.closed[-1]["reason"]
    assert not b2.can_open(p2["mint"], NOW + 26 * 60 + 10)[0]
    assert b2.can_open(p2["mint"], NOW + 26 * 60 + 21600)[0]


def test_paper_size_stop_unchanged_on_nokey_facts():
    b = MemeBook(config.load(), _Mem(), on_event=lambda e: None)
    pos, msg = b.open_pos(nokey(), "no-key flow", NOW)
    assert msg == "opened"
    start = config.load()["paper"]["start_balance"]
    assert pos["notional0"] == pytest_approx(start * 1.5 / 100, rel=0.02)
    assert pos["stop"] == pytest_approx(pos["entry"] * 0.90, rel=0.01)


def pytest_approx(v, rel=1e-9):
    import pytest
    return pytest.approx(v, rel=rel)

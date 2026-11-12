"""gmgn_meme: fail-closed gates, paper fills, no signing, futures strategies still selectable."""
import asyncio, os, time
from futbot import config, meme_rules as R
from futbot.gmgn_http import GmgnClient, GmgnError, DEMO_KEYS
from futbot.meme_paper import MemeBook, buy_fill, sell_fill, slippage_pct
from futbot.meme_sources import apply_rugcheck, apply_security, assemble, from_dex_pair, smart_buyers_from_traders
from test_donchian import FakeFeed, engine, setup

G = config.load()["strategy"]["gmgn"]
NOW = 1_700_000_000.0

def good():
    return {
        "mint": "MINT" + "1" * 28, "symbol": "CAT", "price": 0.002, "mcap": 150_000,
        "liquidity": 40_000, "volume": 25_000, "volume_window": "h1", "age_sec": 900,
        "chg_1h": 18.0, "chg_5m": 4.0, "buys": 40, "sells": 20,
        "mint_revoked": True, "freeze_revoked": True, "burn_ratio": 1.0, "lp_locked_pct": None,
        "top10": 0.22, "dev_hold": 0.03, "bundler_rate": 0.05, "bundler_wallets": 1,
        "sniper_count": 3, "honeypot": False, "sell_tax": 0.0,
        "smart_buyers": ["WalletA", "WalletB"],
        "dev": {"analyzed": 4, "alive": 4, "rugged": 0, "rug_rate": 0.0, "own_img_reuse": 0,
                "exited": False, "source": "created_tokens"},
        "sources": {k: "test" for k in ("mint_revoked", "freeze_revoked", "lp", "top10", "dev_hold",
                                        "bundler_rate", "sniper_count", "honeypot", "sell_tax",
                                        "liquidity", "volume", "age", "mcap", "smart_money")},
        "conflicts": [],
    }

class Mem:
    def __init__(self):
        self.d = {}
    def get(self, k, default=None):
        return self.d.get(k, default)
    def put(self, k, v):
        self.d[k] = v

def book():
    c = config.load()
    b = MemeBook(c, Mem(), on_event=lambda e: None)
    return b

def test_pass_and_each_missing_field_rejects():
    assert R.evaluate(good(), G)["ok"]
    keys = ["mint_revoked", "freeze_revoked", "burn_ratio", "top10", "dev_hold", "bundler_rate",
            "sniper_count", "honeypot", "sell_tax", "liquidity", "volume", "age_sec", "mcap",
            "chg_1h", "chg_5m", "buys", "smart_buyers"]
    for k in keys:
        f = good(); f[k] = None
        if k == "burn_ratio":
            f["lp_locked_pct"] = None
        if k == "bundler_rate":
            f["bundler_wallets"] = None
        if k == "smart_buyers":
            f["smart_degen_count"] = None
        d = R.evaluate(f, G)
        assert d["ok"] is False and d["decision"] == "reject", k
        assert d["reasons"], k

def test_honeypot_unknown_is_not_a_quote():
    f = good(); f["honeypot"] = None
    d = R.evaluate(f, G)
    assert not d["ok"]
    assert any("quote" in r.lower() or "unknown" in r.lower() for r in d["reasons"])
    f["honeypot"] = True
    assert not R.evaluate(f, G)["ok"]
    f = good(); f["honeypot"] = False
    assert R.evaluate(f, G)["ok"]
    # price-only payload (what DexScreener can prove)
    bare = from_dex_pair({"baseToken": {"address": "A" * 32, "symbol": "X"}, "priceUsd": "0.01",
                          "marketCap": 100000, "liquidity": {"usd": 50000}, "volume": {"h1": 20000},
                          "priceChange": {"h1": 10, "m5": 2}, "txns": {"h1": {"buys": 10, "sells": 4}},
                          "pairCreatedAt": (time.time() - 3600) * 1000})
    d = R.evaluate(bare, G)
    assert not d["ok"]
    assert any("honeypot" in r.lower() for r in d["reasons"])
    assert d["score_note"].startswith("display only")

def test_smart_money_distinct_and_chase():
    f = good(); f["smart_buyers"] = ["WalletA", "WalletA"]
    assert not R.evaluate(f, G)["ok"]
    f = good(); f["smart_buyers"] = None; f["smart_degen_count"] = 2
    d = R.evaluate(f, G)
    assert d["ok"] and "aggregate" in d["entry"]
    f = good(); f["chg_1h"] = 120
    assert any("chase" in r.lower() for r in R.evaluate(f, G)["reasons"])
    f = good(); f["chg_1h"] = 1; f["chg_5m"] = -1
    assert any("momentum" in r.lower() for r in R.evaluate(f, G)["reasons"])

def test_dev_history_needs_a_token_list():
    assert R.dev_from_tokens([], {"open_count": 50})["rug_rate"] is None
    alive = [{"is_open": True, "liquidity_less_4k": False, "logo": "a"} for _ in range(3)]
    dead = [{"is_open": False, "liquidity_less_4k": True, "logo": "a"}]
    dev = R.dev_from_tokens(alive + dead, {"creator": "C"})
    assert dev["analyzed"] == 4 and dev["alive"] == 3 and dev["rug_rate"] == 0.25 and dev["own_img_reuse"] == 3
    f = good(); f["dev"] = dev
    assert not R.evaluate(f, G)["ok"]  # reskin 3 > 2
    dev["own_img_reuse"] = 0
    f["dev"] = dev
    assert R.evaluate(f, G)["ok"]
    dev["rug_rate"] = 0.8; dev["alive"] = 0
    f["dev"] = dev
    assert any("rug" in r.lower() for r in R.evaluate(f, G)["reasons"])

def test_wallet_style_tags_do_not_pass_a_gate():
    tags = R.wallet_style_tags(
        {"trades": 20, "token_num": 10, "winrate": 0.7, "avg_hold_s": 6 * 86400, "avg_buy_usd": 8000,
         "realized_profit": 1000, "created_token_count": 0, "dist": {"lt_n50": 0, "gt_5": 1, "x2_5": 1}},
        {"entry_under_100k": 0.9, "fast_flip_rate": 0.0, "median_entry_mcap": 20000})
    assert "sniper" in tags and "diamond" in tags and "whale" in tags and "small_mcap" in tags
    bot = R.wallet_style_tags({"trades": 3000, "token_num": 100, "winrate": 0.2, "avg_hold_s": 10,
                               "avg_buy_usd": 10, "realized_profit": -1, "created_token_count": 80,
                               "dist": {"lt_n50": 40}}, {"entry_under_100k": 0.1, "fast_flip_rate": 0.5, "median_entry_mcap": 1e6})
    assert "bot" in bot and "dev" in bot and "sniper" not in bot
    buyers, _ = smart_buyers_from_traders([
        {"address": "A", "maker_token_tags": ["smart_degen"], "buy_volume_cur": 5, "sell_volume_cur": 1},
        {"address": "A", "maker_token_tags": ["smart_degen"], "buy_volume_cur": 2, "sell_volume_cur": 0},
        {"address": "B", "tags": ["sniper"], "buy_volume_cur": 9, "sell_volume_cur": 0},
        {"address": "C", "maker_token_tags": ["smart_degen"], "buy_volume_cur": 1, "sell_volume_cur": 4},
    ])
    assert buyers == ["A"]

def test_sources_disagree_and_rugcheck_does_not_invent_honeypot():
    assert R.merge_tri(True, False) == "conflict"
    facts = {"sources": {}}
    apply_security(facts, {"renounced_mint": True, "is_honeypot": ""}, "gmgn")
    assert facts["mint_revoked"] is True and "honeypot" not in facts
    apply_rugcheck(facts, {"token": {"mintAuthority": None, "freezeAuthority": "still-set"},
                           "risks": [{"name": "Low liquidity"}], "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                           "transferFee": None, "markets": [{"lp": {"lpLockedPct": 99}}]})
    assert facts["mint_revoked"] is True and facts["freeze_revoked"] is False
    assert facts.get("honeypot") is None  # no honeypot phrase is not a pass
    assert facts["sell_tax"] == 0.0 and facts["sources"]["sell_tax"] == "rugcheck_spl_no_transfer_fee"
    assert facts["lp_locked_pct"] == 99

def test_client_blocks_swap_demo_key_and_redacts():
    assert "gmgn_basesolbscethmonadtron" in DEMO_KEYS
    calls = []
    c = GmgnClient(key="gmgn_basesolbscethmonadtron", fetch=lambda *a, **k: calls.append(1))
    assert c.key == "" and c.refused_demo
    try:
        c.get("/v1/token/info", {"chain": "sol", "address": "x"})
        assert False
    except GmgnError as e:
        assert "demo" in str(e)
    c2 = GmgnClient(key="unit-test-key", fetch=lambda *a, **k: calls.append(1), clock=lambda: 1)
    try:
        c2.get("/v1/trade/swap", {})
        assert False
    except GmgnError:
        pass
    assert calls == []

    def fetch(method, url, headers):
        assert method == "GET" and "unit-test-key" not in url and "X-Signature" not in headers
        assert headers["X-APIKEY"] == "unit-test-key" and "timestamp=1" in url and "client_id=" in url
        return 200, {"code": 5, "message": "nope unit-test-key"}
    c3 = GmgnClient(key="unit-test-key", fetch=fetch, clock=lambda: 1)
    try:
        c3.get("/v1/token/security", {"chain": "sol", "address": "M"})
        assert False
    except GmgnError as e:
        assert "unit-test-key" not in str(e)

def test_paper_fills_slippage_fees_and_exits():
    assert slippage_pct(10, None, 0.8, 1, 8) is None
    assert buy_fill(1, 100, None, 0.3, 0.15, 0.8, 1, 8) is None
    huge = buy_fill(1, 100, 1_000_000, 0.3, 0.15, 0.8, 1, 8)
    assert huge["price"] == pytest_approx(1.008) and huge["fee"] > 0 and huge["qty"] < 100
    taxed = sell_fill(1, 10, 1_000_000, 0.3, 0, 0, 0, 8, sell_tax=0.05)
    clean = sell_fill(1, 10, 1_000_000, 0.3, 0, 0, 0, 8, sell_tax=0)
    assert taxed["proceeds"] < clean["proceeds"]
    b = book(); f = good(); pos, msg = b.open_pos(f, "test", NOW)
    assert pos and msg == "opened"
    spent = config.load()["paper"]["start_balance"]
    assert b.equity() > spent - 5  # fee+slippage, not the whole position removed from equity
    assert pos["notional0"] == pytest_approx(spent * 1.5 / 100, rel=0.02)
    assert pos["entry"] > f["price"] and pos["fees"] > 0
    b.mark(pos["mint"], pos["entry"] * 1.30, f["liquidity"], NOW + 60)
    assert pos["mint"] in b.positions and b.positions[pos["mint"]]["qty"] < pos["qty0"]
    assert b.positions[pos["mint"]]["stop"] > pos["stop0"]
    b2 = book(); p2, _ = b2.open_pos(good(), "t", NOW)
    b2.mark(p2["mint"], p2["stop"] * 0.99, 40_000, NOW + 10)
    assert p2["mint"] not in b2.positions and b2.closed[-1]["pnl"] < 0 and b2.closed[-1]["fees"] > 0
    b3 = book(); p3, _ = b3.open_pos(good(), "t", NOW)
    b3.positions[p3["mint"]]["opened"] = NOW - 40 * 60
    b3.mark(p3["mint"], p3["entry"] * 0.99, 40_000, NOW)
    assert "time stop below entry" in b3.closed[-1]["reason"]
    b3b = book(); p3b, _ = b3b.open_pos(good(), "t", NOW)
    b3b.positions[p3b["mint"]]["opened"] = NOW - 40 * 60
    b3b.mark(p3b["mint"], p3b["entry"] * 1.02, 40_000, NOW)
    assert p3b["mint"] in b3b.positions and b3b.positions[p3b["mint"]]["stop"] > p3b["stop0"]
    b4 = book(); p4, _ = b4.open_pos(good(), "t", NOW)
    b4.mark(p4["mint"], p4["entry"], 1_000, NOW + 5)
    assert "liquidity drop" in b4.closed[-1]["reason"]
    b5 = book(); p5, _ = b5.open_pos(good(), "t", NOW)
    b5.mark(p5["mint"], p5["entry"], 40_000, NOW + 5, stale=True)
    assert "stale" in b5.closed[-1]["reason"]
    b6 = book(); b6.breakers.roll(NOW, 1000); b6.breakers.day_start_eq = 1000; b6.balance = 900
    b6.breakers.check_daily(b6.equity())
    assert b6.breakers.tripped and not b6.can_open("Z", NOW)[0]
    b7 = book()
    for _ in range(3):
        b7.breakers.on_close(-1, NOW)
    assert b7.breakers.tripped and "consecutive" in b7.breakers.tripped

def pytest_approx(v, rel=1e-9):
    import pytest
    return pytest.approx(v, rel=rel)

def test_meme_engine_skips_futures_entries_and_report_has_risk_metrics():
    b1, b4 = setup(); e = engine(FakeFeed(b1, b4), "gmgn_meme")
    asyncio.run(e.evaluate("BTC"))
    assert not e.pf.positions
    text = e.report_text()
    assert "Risk metrics" in text and "Memecoin fills" in text
    assert config.set_key(config.load(), "strategy.gmgn.position_size_pct", "9") == 2.0

def test_history_lists_rejects(tmp_path, monkeypatch):
    import adapter
    from futbot.store import Store
    monkeypatch.setattr(adapter, "DATA", str(tmp_path))
    st = Store(os.path.join(tmp_path, "futbot.db"))
    st.signal_log("CAT", 0, "gmgn_meme", "MINT | honeypot unknown", "reject")
    h = adapter.history()
    assert h["rejected"] and "honeypot unknown" in h["rejected"][0]["reason"]

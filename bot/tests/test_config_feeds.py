import json, os, shutil, pytest, yaml
from futbot import config, feeds

def test_default_config_is_bulk_paper():
    c = config.load()
    assert c["mode"] == "paper" and c["venue"] == "bulk" and c["symbols"] == ["BTC", "ETH", "SOL"]
    assert c["data"]["sources"][0] == "bulk" and c["fees"]["taker_fee_pct"] == 0.035 and c["fees"]["maker_fee_pct"] == 0
    s = open(os.path.join(os.path.dirname(config.DEFAULT), "config.yaml")).read().lower()
    for bad in ("private_key", "secret", "api_key", "seed"): assert bad not in s

def test_live_config_cannot_unlock(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text(yaml.safe_dump({"mode": "live", "risk": {"leverage": 20}, "enabled_symbols": ["BTC", "DOGE"]}))
    c = config.load(str(p)); assert c["mode"] == "paper" and c["risk"]["leverage"] == 3 and c["enabled_symbols"] == ["BTC"]
    assert "strategy" in c and "exits" in c                                       # merged with defaults

def test_set_key_locks_and_clamps():
    c = config.load()
    for k in ("mode", "paper.start_balance", "fees.taker_fee_pct", "data.sources"):
        with pytest.raises(ValueError): config.set_key(c, k, "1")
    assert config.set_key(c, "risk.leverage", "10") == 3.0 and config.set_key(c, "risk.risk_pct", "9") == 2.0
    with pytest.raises(KeyError): config.set_key(c, "risk.nope", "1")

def test_live_gate_defaults_migrate_once(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("""
mode: paper
defaults_rev: 3
venue: bulk
risk:
  risk_pct: 0.5
strategy:
  name: hf_maker
  gmgn:
    min_liquidity_usd: 15000   # old shipped default
    min_volume_usd: 20000
    nokey_min_buy_sell_ratio: 1.3
    max_top10_pct: 40
    min_age_sec: 180
    max_age_sec: 172800
    min_momentum_pct: 5
    max_chase_pct: 80
    position_size_pct: 1.5
    stop_loss_pct: 12
    min_burn_pct: 95
    min_lp_locked_pct: 80
""")
    c = config.load(str(p))
    g = c["strategy"]["gmgn"]
    assert g["min_liquidity_usd"] == 8000
    assert g["min_volume_usd"] == 20000
    assert g["nokey_min_buy_sell_ratio"] == 1.1
    assert g["max_top10_pct"] == 50
    assert g["min_age_sec"] == 120 and g["max_age_sec"] == 259200
    assert g["min_momentum_pct"] == 3 and g["max_chase_pct"] == 100
    assert g["position_size_pct"] == 1.5 and g["stop_loss_pct"] == 12
    assert g["min_burn_pct"] == 95 and g["min_lp_locked_pct"] == 80
    assert g["gates_rev"] == 2
    assert c["strategy"]["name"] == "hf_maker" and c["risk"]["risk_pct"] == 0.5
    assert c["defaults_rev"] == 3 and c["mode"] == "paper"
    text = p.read_text()
    assert "old shipped default" in text
    assert "min_volume_usd: 20000" in text and "stop_loss_pct: 12" in text
    assert "hf_maker" in text and "gates_rev: 2" in text
    p.write_text(text.replace("min_liquidity_usd: 8000", "min_liquidity_usd: 15000"))
    assert config.load(str(p))["strategy"]["gmgn"]["min_liquidity_usd"] == 15000

def test_bulk_ws_parse_and_subs():
    b = feeds.Bulk(None)
    sub = json.loads(b.ws_subs(["BTC", "SOL"])[0]); assert sub["subscription"][1] == {"type": "ticker", "symbol": "SOL-USD"}
    msg = json.dumps({"type": "ticker", "data": {"ticker": {"symbol": "BTC-USD", "lastPrice": 77900.5, "markPrice": 77901.0}}})
    assert b.ws_parse(msg) == [("BTC", 77900.5, 77901.0)]
    assert b.ws_parse(json.dumps({"type": "pong"})) == []

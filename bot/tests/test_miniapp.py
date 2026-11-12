import hashlib, hmac, json, os, time, urllib.parse, pytest
from fastapi.testclient import TestClient
import server, adapter
from miniapp import settings_spec
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

def init_data(uid=123456789, age=0):
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "q", "user": json.dumps({"id": uid, "first_name": "D"})}
    chk = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    sec = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(sec, chk.encode(), hashlib.sha256).hexdigest(); return urllib.parse.urlencode(pairs)

c = TestClient(server.app)

def test_auth_required_and_only_owner():
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Telegram-Init-Data": init_data(uid=42)}).status_code == 403
    assert c.get("/api/status", headers={"X-Telegram-Init-Data": init_data(age=90000)}).status_code == 401
    bad = init_data().replace("hash=", "hash=00"); assert c.get("/api/status", headers={"X-Telegram-Init-Data": bad}).status_code == 401
    r = c.get("/api/status", headers={"X-Telegram-Init-Data": init_data()}); assert r.status_code == 200
    j = r.json(); assert j["demo"] and j["source"] == "bulk" and "BNB" not in j["symbols"]

def test_dev_no_auth_only_without_db(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1"); assert c.get("/api/signals").status_code == 200
    monkeypatch.setattr(adapter, "available", lambda: True); assert c.get("/api/signals").status_code == 401

def test_forbidden_settings_keys():
    h = {"X-Telegram-Init-Data": init_data()}
    for k in ("mode", "paper.start_balance", "fees.taker_fee_pct", "data.sources", "api_key"):
        assert c.post("/api/settings", headers=h, json={"changes": {k: 1}}).status_code in (400, 403, 422)

def test_settings_validation():
    assert settings_spec.validate("risk.leverage", 3) == 3
    with pytest.raises(ValueError): settings_spec.validate("risk.leverage", 5)
    assert settings_spec.validate("enabled_symbols", ["SOL", "BTC"]) == ["BTC", "SOL"]
    with pytest.raises(ValueError): settings_spec.validate("enabled_symbols", ["BNB"])
    assert settings_spec.validate("enabled_symbols", ["HYPE"], ["BTC", "HYPE"]) == ["HYPE"]

def test_manual_preview_demo_and_bad_symbol():
    h = {"X-Telegram-Init-Data": init_data()}
    r = c.post("/api/manual/preview", headers=h, json={"symbol": "SOL-USD", "side": "long"}); assert r.status_code == 200
    j = r.json(); assert j["lev"] <= 3 and j["est_fees"] > 0
    assert c.post("/api/manual/preview", headers=h, json={"symbol": "DOGE", "side": "long"}).status_code == 422

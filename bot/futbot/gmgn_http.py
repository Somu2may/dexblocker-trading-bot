"""Read-only GMGN OpenAPI client (https://openapi.gmgn.ai).

Auth for these GETs is the X-APIKEY header plus a fresh timestamp and client_id
query (GMGN "exist" auth). This module never signs a request and never calls a
swap, quote, order, or token-creation path. The key is read from the environment
and is never written to logs, URLs, or exception text.

The public demo key is refused. Query endpoints do not prove a token can be sold.
"""
import os, time, uuid
from urllib.parse import urlencode

HOST = "https://openapi.gmgn.ai"
# Published demo key from GMGN's Base skill page. Never send it.
DEMO_KEYS = frozenset({"gmgn_basesolbscethmonadtron"})
# Exist-auth GETs only. Anything under /v1/trade or cooking/create is blocked
# even if someone later adds it to a call site.
ALLOW = frozenset({
    "/v1/token/info",
    "/v1/token/security",
    "/v1/token/pool_info",
    "/v1/market/token_top_holders",
    "/v1/market/token_top_traders",
    "/v1/market/token_kline",
    "/v1/market/rank",
    "/v1/user/smartmoney",
    "/v1/user/kol",
    "/v1/user/created_tokens",
    "/v1/user/wallet_activity",
})


class GmgnError(Exception):
    pass


def _redact(text, key):
    s = str(text)[:240]
    if key and key in s:
        s = s.replace(key, "[redacted]")
    return s


class GmgnClient:
    def __init__(self, key=None, base=HOST, fetch=None, clock=None):
        raw = key if key is not None else (os.environ.get("GMGN_API_KEY") or "")
        raw = str(raw).strip().strip("\"'")
        self.refused_demo = raw in DEMO_KEYS
        self.key = "" if self.refused_demo else raw
        self.base = (base or HOST).rstrip("/")
        self.fetch = fetch  # (method, url, headers) -> (status_int, json_obj)
        self.clock = clock or time.time

    @property
    def enabled(self):
        return bool(self.key)

    def get(self, path, query=None):
        if path not in ALLOW or "/trade/" in path or "swap" in path or "sign" in path or path.endswith("/quote"):
            raise GmgnError("blocked endpoint")
        if self.refused_demo:
            raise GmgnError("refusing the public demo key")
        if not self.key:
            raise GmgnError("GMGN key is not set")
        q = {k: v for k, v in (query or {}).items() if v is not None}
        q["timestamp"] = str(int(self.clock()))
        q["client_id"] = str(uuid.uuid4())
        url = f"{self.base}{path}?{urlencode(q, doseq=True)}"
        if self.key in url:
            raise GmgnError("refusing to put the key in the URL")
        headers = {"X-APIKEY": self.key, "Accept": "application/json", "User-Agent": "dexblocker-paper/1.0"}
        if "X-Signature" in headers:
            raise GmgnError("signing is disabled")
        status, obj = self._call(url, headers)
        if not isinstance(obj, dict):
            raise GmgnError(f"{path} bad response HTTP {status}")
        code = obj.get("code")
        if code not in (0, "0", None):
            raise GmgnError(_redact(f"{path} HTTP {status} code {code} {obj.get('message') or obj.get('error') or ''}", self.key))
        if status and int(status) >= 400:
            raise GmgnError(_redact(f"{path} HTTP {status}", self.key))
        return obj.get("data", obj)

    def _call(self, url, headers):
        if self.fetch:
            return self.fetch("GET", url, headers)
        import httpx
        try:
            r = httpx.get(url, headers=headers, timeout=8.0)
        except Exception as e:
            raise GmgnError(_redact(f"network {type(e).__name__}", self.key))
        try:
            body = r.json()
        except Exception:
            raise GmgnError(f"non-json HTTP {r.status_code}")
        return r.status_code, body

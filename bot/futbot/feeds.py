"""Public market data with automatic failover. No API keys, read-only GETs + public websockets.

VENUE = BULK (bulk.trade, Solana perp DEX): candles, ticker (last/mark/oracle/funding), L2 book, exchangeInfo, ticker websocket.
  REST https://mainnet-api1.bulk.trade/api/v1, WS wss://mainnet-ws1.bulk.trade. If mainnet REST fails, exchangeInfo + ORACLE price
  come from https://exchange-api.bulk.trade/api/v1 (that host is BULK *testnet*: its last/mark trade away from mainnet, so only
  the external oracle price is used and the feed is flagged `proxy` -> exits only, no new entries).
Other sources (okx, bybit, binance, hyperliquid) serve as (a) price proxy if BULK is unreachable and (b) reference volume for the
volume filter (BULK's own 1m volume is tiny and often zero). Binance/Bybit usually refuse US cloud IPs - the probe skips them.
Candles: REST (closed bars only, ascending tuples (ts_open_s, o, h, l, c, quote_volume)).
Prices: websocket (last + mark) with REST polling fallback. Funding: REST."""
import asyncio, json, logging, time
import httpx, websockets

log = logging.getLogger("feeds")
UA = {"User-Agent": "dexblocker-paper-bot/1.0 (+public market data)"}
TF = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

class SourceError(Exception): pass

class Source:
    name = "base"; max_limit = 300; funding_hours = 8
    def __init__(self, client): self.c = client
    async def get(self, url, **params):
        r = await self.c.get(url, params=params, headers=UA, timeout=10)
        if r.status_code != 200: raise SourceError(f"{self.name} HTTP {r.status_code}")
        return r.json()
    async def candles(self, sym, tf, n): raise NotImplementedError
    async def prices(self, syms): raise NotImplementedError       # {sym: {"last": x, "mark": y}}
    async def funding(self, sym): raise NotImplementedError       # {"rate": r, "next": ts_s, "hours": h}
    ws_url = None
    def ws_subs(self, syms): return []
    def ws_parse(self, msg): return []                             # [(sym, last|None, mark|None)]
    ws_ping = None

class OKX(Source):
    name = "okx"; max_limit = 300; base = "https://www.okx.com"
    ws_url = "wss://ws.okx.com/ws/v5/public"; ws_ping = "ping"
    def inst(self, s): return f"{s}-USDT-SWAP"
    async def candles(self, sym, tf, n):
        bar = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}[tf]; out, after = [], None
        while len(out) < n:
            p = {"instId": self.inst(sym), "bar": bar, "limit": min(300 if after is None else 100, n - len(out) + 1)}
            if after: p["after"] = after
            j = await self.get(self.base + ("/api/v5/market/candles" if after is None else "/api/v5/market/history-candles"), **p)
            if j.get("code") != "0": raise SourceError(f"okx {j.get('msg')}")
            d = j["data"]
            if not d: break
            out += [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in d if x[8] == "1"]
            after = d[-1][0]
            if len(d) < 2: break
        return sorted(set(out))[-n:]
    async def prices(self, syms):
        j = await self.get(self.base + "/api/v5/market/tickers", instType="SWAP")
        m = await self.get(self.base + "/api/v5/public/mark-price", instType="SWAP")
        mk = {x["instId"]: float(x["markPx"]) for x in m.get("data", [])}
        out = {}
        for x in j.get("data", []):
            for s in syms:
                if x["instId"] == self.inst(s): out[s] = {"last": float(x["last"]), "mark": mk.get(x["instId"], float(x["last"]))}
        return out
    async def funding(self, sym):
        d = (await self.get(self.base + "/api/v5/public/funding-rate", instId=self.inst(sym)))["data"][0]
        return {"rate": float(d["fundingRate"]), "next": int(d["fundingTime"]) // 1000, "hours": 8}
    def ws_subs(self, syms):
        a = [{"channel": "tickers", "instId": self.inst(s)} for s in syms] + [{"channel": "mark-price", "instId": self.inst(s)} for s in syms]
        return [json.dumps({"op": "subscribe", "args": a})]
    def ws_parse(self, msg):
        if msg == "pong": return []
        j = json.loads(msg); ch = (j.get("arg") or {}).get("channel"); out = []
        for d in j.get("data") or []:
            s = d.get("instId", "").split("-")[0]
            if ch == "tickers": out.append((s, float(d["last"]), None))
            elif ch == "mark-price": out.append((s, None, float(d["markPx"])))
        return out

class Bybit(Source):
    name = "bybit"; max_limit = 1000; base = "https://api.bybit.com"
    ws_url = "wss://stream.bybit.com/v5/public/linear"; ws_ping = json.dumps({"op": "ping"})
    async def candles(self, sym, tf, n):
        iv = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}[tf]
        j = await self.get(self.base + "/v5/market/kline", category="linear", symbol=sym + "USDT", interval=iv, limit=min(1000, n + 1))
        if j.get("retCode") != 0: raise SourceError(f"bybit {j.get('retMsg')}")
        now = time.time(); sec = TF[tf]
        rows = [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in j["result"]["list"]]
        return sorted(r for r in rows if r[0] + sec <= now)[-n:]
    async def prices(self, syms):
        j = await self.get(self.base + "/v5/market/tickers", category="linear")
        return {x["symbol"][:-4]: {"last": float(x["lastPrice"]), "mark": float(x["markPrice"])} for x in j["result"]["list"] if x["symbol"][:-4] in syms and x["symbol"].endswith("USDT")}
    async def funding(self, sym):
        x = (await self.get(self.base + "/v5/market/tickers", category="linear", symbol=sym + "USDT"))["result"]["list"][0]
        return {"rate": float(x["fundingRate"]), "next": int(x["nextFundingTime"]) // 1000, "hours": 8}
    def ws_subs(self, syms): return [json.dumps({"op": "subscribe", "args": [f"tickers.{s}USDT" for s in syms]})]
    def ws_parse(self, msg):
        j = json.loads(msg); d = j.get("data")
        if not isinstance(d, dict) or not str(j.get("topic", "")).startswith("tickers."): return []
        s = d.get("symbol", "")[:-4]
        return [(s, float(d["lastPrice"]) if d.get("lastPrice") else None, float(d["markPrice"]) if d.get("markPrice") else None)]

class Binance(Source):
    name = "binance"; max_limit = 1500; base = "https://fapi.binance.com"
    ws_url = None   # set per symbol list
    async def candles(self, sym, tf, n):
        j = await self.get(self.base + "/fapi/v1/klines", symbol=sym + "USDT", interval=tf, limit=min(1500, n + 1))
        if not isinstance(j, list): raise SourceError(f"binance {j}")
        now = time.time() * 1000
        return [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in j if x[6] < now][-n:]
    async def prices(self, syms):
        j = await self.get(self.base + "/fapi/v1/premiumIndex"); t = await self.get(self.base + "/fapi/v1/ticker/price")
        last = {x["symbol"]: float(x["price"]) for x in t}
        return {x["symbol"][:-4]: {"last": last.get(x["symbol"], float(x["markPrice"])), "mark": float(x["markPrice"])} for x in j if x["symbol"][:-4] in syms and x["symbol"].endswith("USDT")}
    async def funding(self, sym):
        x = await self.get(self.base + "/fapi/v1/premiumIndex", symbol=sym + "USDT")
        return {"rate": float(x["lastFundingRate"]), "next": int(x["nextFundingTime"]) // 1000, "hours": 8}
    def ws_for(self, syms):
        return "wss://fstream.binance.com/stream?streams=" + "/".join(f"{s.lower()}usdt@markPrice@1s/{s.lower()}usdt@miniTicker" for s in syms)
    def ws_parse(self, msg):
        d = json.loads(msg).get("data") or {}; s = d.get("s", "")[:-4]
        if d.get("e") == "markPriceUpdate": return [(s, None, float(d["p"]))]
        if d.get("e") == "24hrMiniTicker": return [(s, float(d["c"]), None)]
        return []

class Hyperliquid(Source):
    name = "hyperliquid"; max_limit = 5000; base = "https://api.hyperliquid.xyz/info"; funding_hours = 1
    ws_url = "wss://api.hyperliquid.xyz/ws"; ws_ping = json.dumps({"method": "ping"})
    async def post(self, body):
        r = await self.c.post(self.base, json=body, headers=UA, timeout=10)
        if r.status_code != 200: raise SourceError(f"hyperliquid HTTP {r.status_code}")
        return r.json()
    async def candles(self, sym, tf, n):
        now = int(time.time()); sec = TF[tf]
        j = await self.post({"type": "candleSnapshot", "req": {"coin": sym, "interval": tf, "startTime": (now - sec * (n + 2)) * 1000, "endTime": now * 1000}})
        return [(int(x["t"]) // 1000, float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"]) * float(x["c"])) for x in j if int(x["T"]) // 1000 < now][-n:]
    async def _ctx(self):
        meta, ctx = await self.post({"type": "metaAndAssetCtxs"})
        return {u["name"]: c for u, c in zip(meta["universe"], ctx)}
    async def prices(self, syms):
        c = await self._ctx()
        return {s: {"last": float(c[s].get("midPx") or c[s]["markPx"]), "mark": float(c[s]["markPx"])} for s in syms if s in c}
    async def funding(self, sym):
        c = (await self._ctx())[sym]; now = time.time()
        return {"rate": float(c["funding"]), "next": int(now // 3600 * 3600 + 3600), "hours": 1}
    def ws_subs(self, syms): return [json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}})]
    def ws_parse(self, msg):
        j = json.loads(msg)
        if j.get("channel") != "allMids": return []
        mids = j["data"]["mids"]; return [(s, float(v), None) for s, v in mids.items() if s in self._syms]

class Bulk(Source):
    name = "bulk"; max_limit = 5000; funding_hours = 1
    bases = ["https://mainnet-api1.bulk.trade/api/v1"]; fallback = "https://exchange-api.bulk.trade/api/v1"
    ws_url = "wss://mainnet-ws1.bulk.trade"
    def inst(self, s): return f"{s}-USD"
    async def candles(self, sym, tf, n):
        sec = TF[tf]; now = int(time.time()); out = {}; end = now
        while len(out) < n:
            start = end - sec * min(5000, n - len(out) + 2)
            j = await self.get(self.bases[0] + "/klines", symbol=self.inst(sym), interval=tf, startTime=start * 1000, endTime=end * 1000)
            if not isinstance(j, list): raise SourceError(f"bulk klines {str(j)[:60]}")
            for x in j:
                if x["T"] // 1000 <= now: out[x["t"] // 1000] = (x["t"] // 1000, float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"]) * float(x["c"]))
            if not j or start <= 0: break
            end = start
        return [out[k] for k in sorted(out)][-n:]
    async def ticker(self, sym):
        try: return await self.get(self.bases[0] + f"/ticker/{self.inst(sym)}"), False
        except Exception:
            t = await self.get(self.fallback + f"/ticker/{self.inst(sym)}")      # testnet: oracle price only
            return {**t, "lastPrice": t["oraclePrice"], "markPrice": t["oraclePrice"]}, True
    async def prices(self, syms):
        out = {}; self.oracle_only = False
        for s in syms:
            t, fb = await self.ticker(s); self.oracle_only |= fb
            out[s] = {"last": float(t["lastPrice"]), "mark": float(t["markPrice"]), "oracle": float(t.get("oraclePrice") or 0), "funding": float(t.get("fundingRate") or 0)}
        return out
    async def funding(self, sym):
        t, _ = await self.ticker(sym)
        return {"rate": float(t["fundingRate"]), "next": int(time.time() // 3600 * 3600 + 3600), "hours": 1}
    async def exchange_info(self):
        for b in self.bases + [self.fallback]:
            try:
                j = await self.get(b + "/exchangeInfo")
                return {x["baseAsset"]: x for x in j if x.get("quoteAsset") == "USD"}
            except Exception as e: err = e
        raise SourceError(f"exchangeInfo: {err}")
    async def book(self, sym, n=20):
        j = await self.get(self.bases[0] + "/l2book", type="l2book", coin=self.inst(sym), nlevels=n)
        bids, asks = j["levels"]; return [(float(l["px"]), float(l["sz"])) for l in bids], [(float(l["px"]), float(l["sz"])) for l in asks]
    def ws_subs(self, syms): return [json.dumps({"method": "subscribe", "subscription": [{"type": "ticker", "symbol": self.inst(s)} for s in syms]})]
    def ws_parse(self, msg):
        j = json.loads(msg)
        if j.get("type") != "ticker": return []
        t = (j.get("data") or {}).get("ticker") or {}; s = t.get("symbol", "").split("-")[0]
        return [(s, float(t["lastPrice"]) if t.get("lastPrice") else None, float(t["markPrice"]) if t.get("markPrice") else None)]

def book_slippage(levels, notional, ref):
    """Average fill vs ref price (fraction) when sweeping `notional` USD through one side of the book. None if the book is too thin."""
    rem, cost, qty = notional, 0.0, 0.0
    for px, sz in levels:
        take = min(rem, px * sz); cost += take; qty += take / px; rem -= take
        if rem <= 1e-9: break
    if rem > 1e-9 or not qty: return None
    return abs(cost / qty / ref - 1)

SOURCES = {"bulk": Bulk, "okx": OKX, "bybit": Bybit, "binance": Binance, "hyperliquid": Hyperliquid}

class Feed:
    """Keeps last/mark price per symbol fresh (websocket, REST fallback) and serves candles/funding with failover."""
    def __init__(self, cfg, client=None, sources=None, name="venue"):
        self.cfg = cfg; self.d = cfg["data"]; self.syms = list(cfg["symbols"]); self.name = name
        self.client = client or httpx.AsyncClient(headers=UA, timeout=10, follow_redirects=True)
        self.order = [n for n in (sources or self.d["sources"]) if n in SOURCES]
        self.srcs = {n: SOURCES[n](self.client) for n in self.order}
        for s in self.srcs.values(): s._syms = set(self.syms)
        self.active = None; self.status = {}; self.price = {}; self.mark = {}; self.tick = {}; self.ws_ok = False
        self.errors = 0; self.funding = {}; self._ws_task = None; self.switches = 0; self.oracle = {}

    @property
    def proxy(self):
        """Reason string when prices do NOT come from the primary venue (exits still managed, no new entries)."""
        if not self.active: return "no market data source"
        if self.active != self.order[0]: return f"{self.order[0]} unreachable, using {self.active} prices as proxy"
        if getattr(self.srcs[self.active], "oracle_only", False): return f"{self.order[0]} mainnet REST down, using oracle price"
        return None

    async def probe(self):
        """Try every source (candles + prices) and pick the first that works from this IP. Returns {name: 'ok'|'error ...'}."""
        res = {}
        for n in self.order:
            t = time.time()
            try:
                c = await self.srcs[n].candles("BTC", "1m", 5); p = await self.srcs[n].prices(self.syms)
                if not c or "BTC" not in p: raise SourceError("empty answer")
                res[n] = f"ok {int((time.time() - t) * 1000)}ms BTC {p['BTC']['last']:.1f}"
                if self.active is None: self.active = n
            except Exception as e: res[n] = f"error {str(e)[:80]}"
        self.status = res; log.info("source probe: %s -> active=%s", res, self.active)
        return res

    @property
    def src(self): return self.srcs[self.active] if self.active else None

    async def _failover(self, why):
        log.warning("source %s failed (%s), failing over", self.active, why)
        old = self.active; self.active = None; await self.probe()
        if self.active and self.active != old:
            self.switches += 1; self.restart_ws()
        return self.active

    async def call(self, fn, *a):
        for attempt in range(2):
            if not self.src and not await self._failover("none active"): raise SourceError("no market data source reachable")
            try:
                r = await getattr(self.src, fn)(*a); self.errors = 0; return r
            except Exception as e:
                self.errors += 1
                if self.errors >= 3 or attempt == 1: await self._failover(str(e)[:60]); self.errors = 0
                else: await asyncio.sleep(1)
        raise SourceError(f"{fn} failed on all sources")

    async def candles(self, sym, tf, n): return await self.call("candles", sym, tf, n)

    async def refresh_funding(self):
        for s in self.syms:
            try: self.funding[s] = await self.call("funding", s)
            except Exception as e: log.warning("funding %s: %s", s, e)

    def _on_tick(self, sym, last, mark):
        now = time.time()
        if last: self.price[sym] = last; self.tick[sym] = now
        if mark: self.mark[sym] = mark; self.tick.setdefault(sym, now)

    def age(self, sym): return time.time() - self.tick.get(sym, 0)

    def restart_ws(self):
        if self._ws_task: self._ws_task.cancel()
        self._ws_task = asyncio.create_task(self._ws())

    async def _ws(self):
        while True:
            src = self.src
            url = src.ws_for(self.syms) if src and isinstance(src, Binance) else (src.ws_url if src else None)
            if not url: await asyncio.sleep(5); continue
            try:
                async with websockets.connect(url, open_timeout=10, ping_interval=None, max_size=2 ** 22) as ws:
                    for m in src.ws_subs(self.syms): await ws.send(m)
                    self.ws_ok = True; log.info("websocket connected: %s", src.name); last_ping = time.time()
                    while True:
                        try: msg = await asyncio.wait_for(ws.recv(), timeout=20)
                        except asyncio.TimeoutError: msg = None
                        if src.ws_ping and time.time() - last_ping > 20: await ws.send(src.ws_ping); last_ping = time.time()
                        if msg is None: continue
                        for sym, last, mark in src.ws_parse(msg): self._on_tick(sym, last, mark)
                        if src is not self.src: break      # failed over -> reconnect to new source
            except asyncio.CancelledError: self.ws_ok = False; raise
            except Exception as e: log.warning("websocket %s: %s", getattr(src, "name", "?"), str(e)[:100])
            self.ws_ok = False; await asyncio.sleep(3)

    async def rest_poll(self):
        """REST price fallback: polls whenever the websocket is down or any symbol has not ticked recently."""
        while True:
            try:
                if not self.ws_ok or any(self.age(s) > self.d["rest_poll_sec"] * 2 for s in self.syms):
                    for s, p in (await self.call("prices", self.syms)).items():
                        self._on_tick(s, p["last"], p["mark"])
                        if p.get("oracle"): self.oracle[s] = p["oracle"]
            except Exception as e: log.warning("rest poll: %s", str(e)[:100])
            await asyncio.sleep(self.d["rest_poll_sec"])

    async def close(self):
        if self._ws_task: self._ws_task.cancel()
        await self.client.aclose()

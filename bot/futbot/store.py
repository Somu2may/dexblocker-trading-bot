"""SQLite persistence for the paper futures book (bot/data/futbot.db). The Mini App opens it read-only."""
import json, sqlite3, threading, time
SCHEMA = """
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, symbol TEXT, side INTEGER, entry REAL, exit REAL, qty REAL, notional REAL, lev REAL,
  opened REAL, closed REAL, pnl REAL, fees REAL, funding REAL, r_mult REAL, reason TEXT, setup TEXT, manual INTEGER, tp1 INTEGER);
CREATE INDEX IF NOT EXISTS trades_closed ON trades(closed);
CREATE TABLE IF NOT EXISTS fills(ts REAL, pos_id TEXT, symbol TEXT, kind TEXT, side INTEGER, qty REAL, price REAL, fee REAL, pnl REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS equity(ts REAL PRIMARY KEY, equity REAL, balance REAL);
CREATE TABLE IF NOT EXISTS signals(symbol TEXT PRIMARY KEY, ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS signal_log(ts REAL, symbol TEXT, side INTEGER, setup TEXT, reason TEXT, action TEXT);
CREATE INDEX IF NOT EXISTS signal_log_ts ON signal_log(ts);
"""

class Store:
    def __init__(self, path):
        self.path = path; self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.executescript(SCHEMA); self.db.commit()
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default
    def put(self, k, v):
        with self.lock: self.db.execute("REPLACE INTO state VALUES(?,?)", (k, json.dumps(v, default=str))); self.db.commit()
    def trade(self, t):
        cols = ("id", "symbol", "side", "entry", "exit", "qty", "notional", "lev", "opened", "closed", "pnl", "fees", "funding", "r_mult", "reason", "setup", "manual", "tp1")
        with self.lock:
            self.db.execute(f"REPLACE INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(t.get(c) for c in cols)); self.db.commit()
    def fill(self, ts, pos_id, symbol, kind, side, qty, price, fee, pnl, reason):
        with self.lock:
            self.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)", (ts, pos_id, symbol, kind, side, qty, price, fee, pnl, reason)); self.db.commit()
    def equity(self, ts, eq, bal):
        with self.lock: self.db.execute("REPLACE INTO equity VALUES(?,?,?)", (ts, eq, bal)); self.db.commit()
    def signal(self, symbol, data):
        with self.lock: self.db.execute("REPLACE INTO signals VALUES(?,?,?)", (symbol, time.time(), json.dumps(data, default=str))); self.db.commit()
    def signal_log(self, symbol, side, setup, reason, action):
        with self.lock:
            self.db.execute("INSERT INTO signal_log VALUES(?,?,?,?,?,?)", (time.time(), symbol, side, setup, reason, action))
            self.db.execute("DELETE FROM signal_log WHERE ts < ?", (time.time() - 7 * 86400,)); self.db.commit()
    def trades(self, since=0):
        cur = self.db.execute("SELECT * FROM trades WHERE closed >= ? ORDER BY closed", (since,)); cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    def pnl_summary(self, since=0):
        T = self.trades(since); w = [t for t in T if t["pnl"] > 0]
        return {"n": len(T), "wins": len(w), "losses": len(T) - len(w), "pnl": sum(t["pnl"] for t in T), "fees": sum(t["fees"] or 0 for t in T),
                "win_rate": len(w) / len(T) * 100 if T else 0.0}

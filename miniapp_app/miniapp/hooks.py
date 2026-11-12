"""Bridge between the running futures engine and the Mini App API process. All IPC goes through DATA_DIR/miniapp.db:
  status(k,v)  : 'status' = engine.snapshot() JSON, written every 2s (heartbeat, positions with marks, signals, breakers, feed)
  control(k,v) : 'running' ('1'/'0') and 'reload' (settings changed) written by the API
  manual_req   : manual paper requests (id = idempotency key) executed here, result written back
PAPER only: nothing here can change mode, balances, fees or keys."""
import asyncio, json, logging, os, sqlite3, time
from .settings_spec import FIELDS, get, put
log = logging.getLogger("miniapp.hooks")
SCHEMA = """CREATE TABLE IF NOT EXISTS status(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS control(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS manual_req(id TEXT PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, status TEXT, result TEXT, done_ts REAL);"""
REQ_MAX_AGE = 45

def connect(data_dir):
    c = sqlite3.connect(os.path.join(data_dir, "miniapp.db"), timeout=5, check_same_thread=False)
    for i in range(40):
        try: c.execute("PRAGMA journal_mode=WAL"); c.executescript(SCHEMA); break
        except sqlite3.OperationalError:
            if i == 39: raise
            time.sleep(0.05)
    return c

def reload_allowed(cfg, config_mod):
    new = config_mod.load()
    for f in FIELDS:
        v = get(new, f["key"])
        if v is not None: put(cfg, f["key"], v)
    cfg["mode"] = "paper"

def install(engine, cfg, data_dir, config_mod):
    db = connect(data_dir)
    last_reload = [db.execute("SELECT v FROM control WHERE k='reload'").fetchone()]
    def apply_control():
        rows = dict(db.execute("SELECT k,v FROM control").fetchall())
        want = rows.pop("running", None)
        if want is not None:
            db.execute("DELETE FROM control WHERE k='running'"); db.commit()
            if want == "1":
                left = engine.resume(); engine.notify("▶️ Auto entries ON (via app)." + (f" Still blocked: {left}" if left else ""))
            else: engine.pause(); engine.notify("⏸ Auto entries paused (via app).")
        rl = rows.get("reload")
        if rl is not None and (rl,) != last_reload[0]:
            last_reload[0] = (rl,); reload_allowed(cfg, config_mod); engine.notify("⚙️ Settings updated from app.")
    async def worker():
        from futbot.engine import ManualError
        async def run(rid, kind, payload):
            try: res = await engine.handle(kind, json.loads(payload or "{}")); st = "done"
            except ManualError as e: res, st = {"error": e.msg, "code": e.code}, "error"
            except Exception as e: log.exception("manual %s", kind); res, st = {"error": f"internal error: {str(e)[:120]}", "code": 500}, "error"
            db.execute("UPDATE manual_req SET status=?, result=?, done_ts=? WHERE id=?", (st, json.dumps(res, default=str), time.time(), rid)); db.commit()
        while True:
            try:
                for rid, ts, kind, payload in db.execute("SELECT id, ts, kind, payload FROM manual_req WHERE status='pending' ORDER BY ts").fetchall():
                    if time.time() - ts > REQ_MAX_AGE:
                        db.execute("UPDATE manual_req SET status='error', result=? WHERE id=?", (json.dumps({"error": "request expired, nothing executed", "code": 408}), rid)); db.commit(); continue
                    db.execute("UPDATE manual_req SET status='running' WHERE id=?", (rid,)); db.commit()
                    asyncio.create_task(run(rid, kind, payload))
            except Exception as e: log.warning("manual worker: %s", e)
            await asyncio.sleep(0.25)
    async def loop():
        asyncio.create_task(worker()); n = 0
        while True:
            try:
                apply_control()
                db.execute("REPLACE INTO status VALUES('status',?)", (json.dumps(engine.snapshot(), default=str),)); db.commit()
                n += 1
                if n % 500 == 0: db.execute("DELETE FROM manual_req WHERE ts < ?", (time.time() - 86400,)); db.commit()
            except Exception as e: log.warning("hooks loop: %s", e)
            await asyncio.sleep(2)
    return loop

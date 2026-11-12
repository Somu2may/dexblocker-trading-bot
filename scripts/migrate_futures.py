"""One-time, idempotent switch of a data dir from the old Solana memecoin/majors bot to the BULK futures paper scalper.
  python scripts/migrate_futures.py /opt/memebot/bot/data [/opt/memebot/bot/config.yaml]
- moves solbot.db (+ -wal/-shm) and the old miniapp.db into data/archive/<timestamp>/   (nothing is deleted)
- backs up the old config.yaml to the same archive folder and installs the new futures config (only if the live config is
  not already a futures config, so later /set or app edits survive re-runs)
- writes data/.migrated_futures"""
import os, shutil, sys, time, yaml
data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "bot", "data")
new_cfg = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "bot", "config.yaml")
os.makedirs(data, exist_ok=True)
arch = os.path.join(data, "archive", time.strftime("%Y%m%d-%H%M%S") + "-memecoin")
moved = []
def mv(name):
    src = os.path.join(data, name)
    if os.path.exists(src):
        os.makedirs(arch, exist_ok=True); shutil.move(src, os.path.join(arch, name)); moved.append(name)
for n in ("solbot.db", "solbot.db-wal", "solbot.db-shm"): mv(n)
if moved:   # old Mini App IPC db has memecoin scanner rows; start clean
    for n in ("miniapp.db", "miniapp.db-wal", "miniapp.db-shm"): mv(n)
live = os.path.join(data, "config.yaml")
cur = {}
if os.path.exists(live):
    try: cur = yaml.safe_load(open(live)) or {}
    except Exception: cur = {}
if cur.get("venue") != "bulk":
    if os.path.exists(live):
        os.makedirs(arch, exist_ok=True); shutil.copy2(live, os.path.join(arch, "config.yaml")); moved.append("config.yaml (copied)")
    shutil.copy2(new_cfg, live)
    print("installed futures config ->", live)
else:
    print("live config already futures/BULK - kept as is")
    # Loosen only the shipped gate numbers that the live file still has. Custom values stay.
    # defaults_rev is not bumped, so a hand-set strategy.name is not pushed over.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
    try:
        from futbot.config import persist_gmgn_gate_defaults
        changed = persist_gmgn_gate_defaults(live)
        if changed is None:
            print("gmgn gate migration: already current")
        else:
            print("gmgn gate migration:", ", ".join(changed) if changed else "stamped, no shipped numbers left to move")
    except Exception as e:
        print("gmgn gate migration failed:", str(e)[:160])
open(os.path.join(data, ".migrated_futures"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
print("archived:", moved or "nothing", "->", arch if moved else "-")

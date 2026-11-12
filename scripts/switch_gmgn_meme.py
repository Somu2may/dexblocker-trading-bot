"""Point a live data dir at gmgn_meme and archive the futures book. Nothing is deleted.

  python scripts/switch_gmgn_meme.py /opt/memebot/bot/data

- moves futbot.db (+ wal/shm) into data/archive/<timestamp>-futures/
- copies the live config into that folder, then sets strategy.name to gmgn_meme
- does not print file contents and does not read or write API keys
"""
import os, shutil, sys, time, yaml

def main():
    data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "bot", "data")
    data = os.path.abspath(data)
    os.makedirs(data, exist_ok=True)
    arch = os.path.join(data, "archive", time.strftime("%Y%m%d-%H%M%S") + "-futures")
    moved = []
    def mv(name):
        src = os.path.join(data, name)
        if os.path.exists(src):
            os.makedirs(arch, exist_ok=True)
            shutil.move(src, os.path.join(arch, name))
            moved.append(name)
    for n in ("futbot.db", "futbot.db-wal", "futbot.db-shm"):
        mv(n)
    live = os.path.join(data, "config.yaml")
    if not os.path.exists(live):
        print("no live config at", live)
        print("archived:", moved or "nothing")
        return
    os.makedirs(arch, exist_ok=True)
    shutil.copy2(live, os.path.join(arch, "config.yaml"))
    with open(live) as f:
        cur = yaml.safe_load(f) or {}
    cur.setdefault("strategy", {})["name"] = "gmgn_meme"
    cur["mode"] = "paper"
    with open(live, "w") as f:
        yaml.safe_dump(cur, f, sort_keys=False)
    print("strategy.name = gmgn_meme")
    print("archived:", moved or "no db", "->", arch)
    print("config copy saved in the archive folder; other settings were left as they were")

if __name__ == "__main__":
    main()

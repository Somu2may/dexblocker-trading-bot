"""One memecoin scan with public data. No Telegram, no wallet, no signing.

  python bot/dry_run_meme.py --limit 8

Uses DexScreener for discovery and prices. Uses RugCheck when it answers.
When GMGN_API_KEY is missing (or is the public demo key), the scan runs in
no-key mode: DexScreener + RugCheck, GMGN-only gates skipped.
Uses GMGN read-only GETs only when GMGN_API_KEY is set to a non-demo key.
Prints how many tokens pass gates and how many pass the entry rule. Does not print the key.
"""
import argparse, logging, os, sys
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
from futbot import config
from futbot.meme_run import scan_and_print

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()
    cfg = config.load()
    cfg["strategy"]["name"] = "gmgn_meme"
    cfg["strategy"].setdefault("gmgn", {})["discover_limit"] = a.limit
    cfg["strategy"]["gmgn"]["enrich_per_scan"] = min(a.limit, int(cfg["strategy"]["gmgn"].get("enrich_per_scan", 8)))
    cfg["strategy"]["gmgn"]["rugcheck_per_scan"] = min(5, a.limit)
    scan_and_print(cfg, limit=a.limit)

if __name__ == "__main__":
    main()

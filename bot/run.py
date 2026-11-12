"""Futures scalper, PAPER ONLY, without the Mini App hooks. Prefer ../launch_bot.py. TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python run.py"""
import os, sys, logging
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from futbot import config
from futbot.store import Store
from futbot.engine import Engine
from futbot.tg import build
os.makedirs(os.path.join(D, "data"), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(D, "data", "bot.log"))])
logging.getLogger("httpx").setLevel(logging.WARNING)
if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"): sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
cfg = config.load()
build(Engine(cfg, Store(os.path.join(D, "data", "futbot.db")), data_dir=os.path.join(D, "data")), cfg).run_polling()

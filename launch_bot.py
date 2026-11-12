"""Combined launcher: futures PAPER engine + Telegram bot + Mini App hooks + menu button.
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, PUBLIC_URL (optional, https), BOT_DIR (default ./bot), MEMEBOT_CONFIG."""
import asyncio, logging, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
BOT = os.environ.get("BOT_DIR", os.path.join(ROOT, "bot"))
sys.path.insert(0, BOT); sys.path.insert(0, os.path.join(ROOT, "miniapp_app"))
DATA = os.environ.get("DATA_DIR", os.path.join(BOT, "data")); os.makedirs(DATA, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(DATA, "bot.log"))])
logging.getLogger("httpx").setLevel(logging.WARNING)
from futbot import config
from futbot.store import Store
from futbot.engine import Engine
from futbot.tg import build
from miniapp import hooks
log = logging.getLogger("launcher")
if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"): sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
cfg = config.load(); cfg["mode"] = "paper"
eng = Engine(cfg, Store(os.path.join(DATA, "futbot.db")), data_dir=DATA)
app = build(eng, cfg)
hook_loop = hooks.install(eng, cfg, DATA, config)
orig_post_init = app.post_init
async def post_init(a):
    await orig_post_init(a)
    asyncio.create_task(hook_loop())
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if url.startswith("https://"):
        from telegram import MenuButtonWebApp, WebAppInfo
        try:
            await a.bot.set_chat_menu_button(chat_id=int(os.environ["TELEGRAM_CHAT_ID"]), menu_button=MenuButtonWebApp("Open App", WebAppInfo(url + "/")))
            log.info("menu button set: %s", url)
        except Exception as e: log.warning("menu button: %s", e)
app.post_init = post_init
app.run_polling(drop_pending_updates=False)

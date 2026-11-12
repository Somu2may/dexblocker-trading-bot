import os, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TMP = tempfile.mkdtemp(prefix="futtest_")
os.environ["DATA_DIR"] = TMP                      # never touch real data
os.environ["MEMEBOT_CONFIG"] = os.path.join(TMP, "config.yaml")
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST-dummy-token-not-real"
os.environ["TELEGRAM_CHAT_ID"] = "123456789"
for p in (os.path.join(ROOT, "bot"), os.path.join(ROOT, "miniapp_app")):
    if p not in sys.path: sys.path.insert(0, p)

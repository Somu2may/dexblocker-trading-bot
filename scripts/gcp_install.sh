#!/bin/bash
# One-shot installer for a Debian 12 VM (GCP e2-micro). Run: curl -fsSL <raw url> | sudo bash
set -e
IP=$(curl -fs -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || curl -fs ifconfig.me)
DOMAIN="$(echo $IP | tr . -).sslip.io"
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip curl procps debian-keyring debian-archive-keyring apt-transport-https gnupg
# 1 GB swap so 1 GB RAM is enough
if [ ! -f /swapfile ]; then fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab; fi
id memebot >/dev/null 2>&1 || useradd -m -s /bin/bash memebot
git config --global --add safe.directory /opt/memebot
if [ -d /opt/memebot/.git ]; then git -C /opt/memebot fetch -q && git -C /opt/memebot reset -q --hard origin/main; else rm -rf /opt/memebot; git clone -q https://github.com/Somu2may/dexblocker-trading-bot.git /opt/memebot; fi
python3 -m venv /opt/memebot/.venv
/opt/memebot/.venv/bin/pip install -q --upgrade pip
/opt/memebot/.venv/bin/pip install -q -r /opt/memebot/requirements.txt
# switch to the BULK futures paper scalper: stop the old bot first, archive solbot.db + old config (never deleted), install new config
systemctl stop memebot 2>/dev/null || true
mkdir -p /opt/memebot/bot/data
/opt/memebot/.venv/bin/python /opt/memebot/scripts/migrate_futures.py /opt/memebot/bot/data /opt/memebot/bot/config.yaml || echo "migration failed (run it manually)"
# prove market data works from THIS server (BULK + fallbacks); output only, never fails the install
/opt/memebot/.venv/bin/python /opt/memebot/scripts/probe_sources.py || true
chown -R memebot:memebot /opt/memebot
touch /etc/memebot.env; chmod 600 /etc/memebot.env
if ! grep -q '^TELEGRAM_CHAT_ID=.' /etc/memebot.env; then
  if [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    printf 'TELEGRAM_CHAT_ID=%s\n' "$TELEGRAM_CHAT_ID" >> /etc/memebot.env
  else
    echo "ERROR: TELEGRAM_CHAT_ID is not set. Export it or add it to /etc/memebot.env. No default is written." >&2
    exit 1
  fi
fi
sed -i '/^PUBLIC_URL=/d;/^PORT=/d;/^TZ=/d' /etc/memebot.env
echo "PUBLIC_URL=https://$DOMAIN" >> /etc/memebot.env; echo "PORT=8080" >> /etc/memebot.env
echo 'TZ=Asia/Kolkata' >> /etc/memebot.env
cat > /etc/systemd/system/memebot.service <<UNIT
[Unit]
Description=DexBlocker BULK futures PAPER scalper + Mini App
After=network-online.target
Wants=network-online.target
[Service]
User=memebot
WorkingDirectory=/opt/memebot
EnvironmentFile=/etc/memebot.env
Environment=PATH=/opt/memebot/.venv/bin:/usr/bin:/bin
ExecStart=/bin/bash /opt/memebot/start.sh
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
# Caddy for free HTTPS (Mini App)
if ! command -v caddy >/dev/null; then
 curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
 curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
 apt-get update -y && apt-get install -y caddy
fi
printf "%s {\n  reverse_proxy 127.0.0.1:8080\n}\n" "$DOMAIN" > /etc/caddy/Caddyfile
systemctl restart caddy
cat > /usr/local/bin/memebot-set-gmgn-key <<'T'
#!/bin/bash
# Write GMGN_API_KEY into /etc/memebot.env without echoing it. Read-only market data only.
read -rsp "Paste GMGN API key (hidden), then Enter: " TOK
echo
TOK=$(printf %s "$TOK" | tr -d "[:space:]\"'")
if [ -z "$TOK" ]; then echo "Empty, nothing saved"; exit 1; fi
if [ "$TOK" = "gmgn_basesolbscethmonadtron" ]; then echo "Refusing the public demo key. Nothing saved."; exit 1; fi
umask 077
touch /etc/memebot.env
chmod 600 /etc/memebot.env
sed -i '/^GMGN_API_KEY=/d' /etc/memebot.env
printf 'GMGN_API_KEY=%s\n' "$TOK" >> /etc/memebot.env
unset TOK
systemctl restart memebot
echo "Saved. Bot restarted. The key was not printed."
T
chmod 755 /usr/local/bin/memebot-set-gmgn-key
cat > /usr/local/bin/memebot-set-token <<'T'
#!/bin/bash
read -rsp "Paste Telegram bot token (hidden), then Enter: " TOK; echo; TOK=$(printf %s "$TOK" | tr -d "[:space:]\"'")
[ -z "$TOK" ] && { echo "Empty, nothing saved"; exit 1; }
sed -i '/^TELEGRAM_BOT_TOKEN=/d' /etc/memebot.env
echo "TELEGRAM_BOT_TOKEN=$TOK" >> /etc/memebot.env
systemctl enable --now memebot && systemctl restart memebot && echo "Saved. Bot started ✅"
T
chmod 755 /usr/local/bin/memebot-set-token
systemctl daemon-reload
echo "INSTALL_DONE domain=https://$DOMAIN"
if grep -q '^TELEGRAM_BOT_TOKEN=' /etc/memebot.env; then systemctl enable --now memebot; systemctl restart memebot; echo BOT_RUNNING; else echo "NEXT: sudo memebot-set-token"; fi
# normalize existing token (strip spaces/CR/quotes)
if grep -q '^TELEGRAM_BOT_TOKEN=' /etc/memebot.env; then V=$(grep '^TELEGRAM_BOT_TOKEN=' /etc/memebot.env | head -1 | cut -d= -f2- | tr -d "[:space:]\"'"); sed -i '/^TELEGRAM_BOT_TOKEN=/d' /etc/memebot.env; echo "TELEGRAM_BOT_TOKEN=$V" >> /etc/memebot.env; systemctl restart memebot; echo TOKEN_NORMALIZED; fi

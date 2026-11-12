#!/bin/bash
# One-container supervisor: FastAPI Mini App (public :$PORT) + BULK futures PAPER scalper with crash-restart and heartbeat watchdog.
# Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (secrets), PUBLIC_URL (https URL of this service), PORT (default 8080).
cd "$(dirname "$0")"; ROOT=$(pwd)
export BOT_DIR="$ROOT/bot" DATA_DIR="$ROOT/bot/data" PORT=${SERVER_PORT:-${PORT:-8080}}
if [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "Set TELEGRAM_CHAT_ID (no hardcoded default)." >&2
  exit 1
fi
mkdir -p "$DATA_DIR"
# config lives on the persistent volume so /set and app edits survive redeploys
[ -f "$DATA_DIR/config.yaml" ] || cp "$BOT_DIR/config.yaml" "$DATA_DIR/config.yaml"
export MEMEBOT_CONFIG="$DATA_DIR/config.yaml"
unset DEV_NO_AUTH
tg() { [ -n "$TELEGRAM_BOT_TOKEN" ] && curl -s -o /dev/null -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" 2>/dev/null; }
api_loop() {
  while true; do
    python -m uvicorn server:app --app-dir "$ROOT/miniapp_app" --host 0.0.0.0 --port "$PORT" --no-access-log --proxy-headers
    echo "$(date -u) api exited ($?), restart in 3s"; sleep 3
  done
}
watchdog() {
  while true; do
    sleep 60
    hb=$(cat "$DATA_DIR/heartbeat" 2>/dev/null || echo 0); age=$(( $(date +%s) - ${hb%.*} ))
    pid=$(pgrep -f "python -u $ROOT/launch_bot.py" | head -1)
    if [ -n "$pid" ] && [ "$age" -gt 300 ]; then
      echo "$(date -u) watchdog: no heartbeat for ${age}s, killing $pid"
      tg "🚨 Watchdog: no heartbeat for $((age/60)) min. Restarting bot."
      kill "$pid"; sleep 5; kill -9 "$pid" 2>/dev/null
    fi
  done
}
trap 'kill $(jobs -p) 2>/dev/null; exit 0' TERM INT
api_loop & watchdog &
n=0
while true; do
  date +%s > "$DATA_DIR/heartbeat"
  python -u "$ROOT/launch_bot.py" 2>&1 | tee -a "$DATA_DIR/stdout.log"
  code=${PIPESTATUS[0]}; n=$((n+1))
  echo "$(date -u) bot exited ($code), restart #$n in 10s"
  tg "⚠️ Bot crashed/stopped (exit $code). Auto-restarting in 10s (restart #$n)."
  sleep 10
done

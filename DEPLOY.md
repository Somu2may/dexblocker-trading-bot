# Deploy on the GCP VM (Debian 12, systemd `memebot`, Caddy HTTPS)

Switching the already-installed VM from futures to the `gmgn_meme` paper scanner (archive the futures DB, set
`strategy.name`, install the read-only GMGN key without echoing it) is `docs/GMGN_MIGRATION.md`. The installer
does not replace a live `config.yaml` whose venue is already `bulk`.

This public repo can be cloned over HTTPS for the install.

```
curl -fsSL "https://raw.githubusercontent.com/Somu2may/dexblocker-trading-bot/main/scripts/gcp_install.sh?x=$RANDOM" | sudo bash
```
The installer:
1. pulls `main`, installs requirements into `/opt/memebot/.venv`;
2. **stops the old bot**, runs `scripts/migrate_futures.py`: moves `bot/data/solbot.db` (+ old `miniapp.db`) and a copy of the
   old `config.yaml` into `bot/data/archive/<timestamp>-memecoin/` (nothing is deleted) and installs the new futures config;
   re-running is safe (a config that already says `venue: bulk` is kept, then a one-time gate migration updates only
   `strategy.gmgn` numbers that are still the previous shipped defaults; `defaults_rev` is not bumped);
3. runs `scripts/probe_sources.py` — prints whether BULK REST/WS and the fallbacks answer **from the VM's IP**;
4. writes the systemd unit + Caddy and restarts the service if the token is set (`sudo memebot-set-token` otherwise).

Check:
```
sudo systemctl status memebot --no-pager
sudo tail -n 50 /opt/memebot/bot/data/bot.log          # "source probe: {... 'bulk': 'ok ...'} -> active=bulk", "websocket connected: bulk"
curl -s https://<ip-with-dashes>.sslip.io/healthz
```
Deploy checks (an older live config is switched once to the current default `hf_maker` via `defaults_rev: 3`; later /set choices stick):
- bot.log every minute: `hf BTC quoting: BUY ... / SELL ...` is DEBUG; check /signals or the Mini App Signals/Chart tabs instead
- probe: `BULK klines BTC 1m` must return bars (hf warm-up uses 600 x 1m)
- probe output: `BULK klines BTC 15m: 600 bars`, `1h: 600 bars`, `4h: 300 bars`, websocket OK, exchangeInfo BTC/ETH/SOL TRADING
- bot.log: `signal BTC waiting: x% below 20x15m high ... · 1h UP/DOWN ...` for all 3 symbols within ~30 s of start (warm-up check)
- Telegram: "🤖 Futures bot online · PAPER" with "Strategy: 🧪 EXPERIMENTAL Donchian-20 15m breakout" and "Auto report: every 2 days at 20:00 IST · next ..."; `/signals`, `/report`
- Mini App: EXP badge in the header, Signals tab shows 20x15m high, distance to break and 1h trend per symbol
- after the next 15-minute mark (+ a few seconds): new `signal ...` lines in bot.log (evaluation on each 15m close) Only ONE process may poll the bot token — never run launch_bot.py elsewhere.

Rollback: the old data is in `bot/data/archive/…`; `git -C /opt/memebot reset --hard <old-commit>`, copy the archived
`config.yaml`/`solbot.db` back, `systemctl restart memebot`.

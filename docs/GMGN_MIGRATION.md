# Switch the VM to `gmgn_meme` (paper memecoins)

The bot is paper-only. This strategy does not hold a wallet and does not sign transactions.
Futures code stays in the tree. `/set strategy.name hf_maker` (or `donchian_15m`, `donchian_1h`, `scalper`) selects it again.
A live `/opt/memebot/bot/data/config.yaml` is kept by `scripts/gcp_install.sh` when `venue` is already `bulk`, including a
`strategy.name` that was set by hand. `defaults_rev` is still `3`, so a hand-set name is not pushed over on the next start.
Gate numbers are a separate one-time migration (`strategy.gmgn.gates_rev`): only a key that still holds the previous
shipped value moves to the moderate default. A number you edited by hand stays.
Run the commands below on the VM to switch.

## 1. Install or update the tree

```bash
curl -fsSL "https://raw.githubusercontent.com/Somu2may/dexblocker-trading-bot/main/scripts/gcp_install.sh?x=$RANDOM" | sudo bash
```

That refreshes `/opt/memebot`, writes `/usr/local/bin/memebot-set-gmgn-key` (same pattern as `memebot-set-token`), and
does **not** replace a futures `config.yaml`. It does not print secrets.

## 2. Stop the bot and archive the futures book

```bash
sudo systemctl stop memebot
sudo /opt/memebot/.venv/bin/python /opt/memebot/scripts/switch_gmgn_meme.py /opt/memebot/bot/data
```

The script moves `futbot.db`, `futbot.db-wal` and `futbot.db-shm` into
`/opt/memebot/bot/data/archive/<timestamp>-futures/` (nothing is deleted), copies the live config into that folder,
then sets `strategy.name` to `gmgn_meme` and leaves every other setting as it was. It does not read or print the env file.

Manual equivalent, if you would rather not run the script:

```bash
sudo systemctl stop memebot
STAMP=$(date +%Y%m%d-%H%M%S)
ARCH=/opt/memebot/bot/data/archive/${STAMP}-futures
sudo mkdir -p "$ARCH"
sudo mv /opt/memebot/bot/data/futbot.db /opt/memebot/bot/data/futbot.db-wal /opt/memebot/bot/data/futbot.db-shm "$ARCH/" 2>/dev/null || true
sudo cp /opt/memebot/bot/data/config.yaml "$ARCH/config.yaml"
sudo python3 - <<'PY'
import yaml
p = "/opt/memebot/bot/data/config.yaml"
c = yaml.safe_load(open(p)) or {}
c.setdefault("strategy", {})["name"] = "gmgn_meme"
c["mode"] = "paper"
yaml.safe_dump(c, open(p, "w"), sort_keys=False)
print("strategy.name =", c["strategy"]["name"])
PY
```

## 3. Add the GMGN key without echoing it

The key is only for read-only GETs (`X-APIKEY` plus `timestamp` and `client_id`). The bot refuses the public demo key
`gmgn_basesolbscethmonadtron` and never calls `/v1/trade/*`, swap, quote, or signing routes.

```bash
sudo memebot-set-gmgn-key
```

Paste the key at the hidden prompt and press Enter. The helper deletes any old `GMGN_API_KEY=` line, appends the new
one to `/etc/memebot.env` (mode 600), unsets the shell variable, restarts `memebot`, and prints `Saved. Bot restarted.
The key was not printed.` An empty paste or the demo key saves nothing.

Do not `echo`, `journalctl` a unit dump of the environment, or `cat /etc/memebot.env`.

Confirm the process is up and that the log line does not contain the key:

```bash
sudo systemctl status memebot --no-pager
sudo journalctl -u memebot -n 40 --no-pager
sudo grep -E 'GMGN_API_KEY=|X-APIKEY' /opt/memebot/bot/data/bot.log && echo FAIL || echo "log has no key assignment"
```

Without a key the scanner runs in **no-key mode** (next section): DexScreener and RugCheck only. Gates that only GMGN
can measure are skipped. A missing value on any gate that still applies still rejects.

## 4. What you should see

Telegram `/status` says memecoins, paper, size 1.5% (default), stop -10%. `/signals` lists the scanner, including
rejects and the reason. `/report` is still the 2-day 20:00 IST report (`report.every_days`, `report.at_ist`) and
includes the same QuantStats-formula block (Sharpe, Sortino, max drawdown, win rate, profit factor).

The Mini App Scanner, Positions and History tabs show memecoin rows. History includes rejected tokens.

Back to futures (the archived db can be copied back first if you want that book):

```bash
sudo systemctl stop memebot
# optional: sudo cp /opt/memebot/bot/data/archive/<timestamp>-futures/futbot.db* /opt/memebot/bot/data/
sudo /opt/memebot/.venv/bin/python - <<'PY'
import yaml
p = "/opt/memebot/bot/data/config.yaml"
c = yaml.safe_load(open(p))
c["strategy"]["name"] = "hf_maker"
yaml.safe_dump(c, open(p, "w"), sort_keys=False)
print("strategy.name = hf_maker")
PY
sudo systemctl start memebot
```

Or, once the meme strategy is loaded, `/set strategy.name hf_maker` switches the loop without editing the file by hand.
Open futures positions are not managed while `gmgn_meme` is selected, which is why the archive step exists.

## Gates (fail-closed)

A missing or non-numeric value rejects. Sources that disagree reject. Display score never overrides a gate.

| Gate | Passes when | Unknown |
|---|---|---|
| Mint authority | GMGN `renounced_mint` true, or RugCheck `mintAuthority` null | Reject |
| Freeze authority | GMGN `renounced_freeze_account` true, or RugCheck `freezeAuthority` null | Reject |
| LP burned or locked | GMGN `burn_status=burn` / `burn_ratio` ≥ 95%, or RugCheck LP locked ≥ 80% | Reject. A pool address alone is not a lock |
| Top 10 holders | GMGN `top_10_holder_rate` or a real holder-percent sum ≤ 50% | Reject |
| Dev history | Creator token list: alive = still open and not liquidity-drained; rug rate = 1 − alive/total ≤ 50%. Artwork reuse and a huge inner-launch count also reject when those fields are present | Reject. A launch count with no per-token list is not enough |
| Dev holding | Dev team / creator balance rate ≤ 10%. Creator already exited this mint rejects when that status is explicit | Reject |
| Bundlers | Bundler rate ≤ 30% and bundler wallet count ≤ 8 | Reject |
| Snipers | GMGN `sniper_count` ≤ 20 | Reject |
| Honeypot | GMGN `is_honeypot` explicitly no/false. RugCheck risk text "honeypot" or "cannot sell" rejects | Reject. Empty string (common on SOL) is unknown. A DexScreener price or a swap quote is not a sell |
| Sell tax | GMGN `sell_tax` ≤ 5%, or RugCheck transfer-fee, or legacy SPL mint whose report includes `transferFee: null` (that program cannot charge a transfer fee) ≤ 5% | Reject |
| Liquidity | ≥ $8,000 from GMGN or DexScreener | Reject |
| Volume | ≥ $6,000. 1h when present; a 24h figure is labeled 24h | Reject |
| Age | 2 minutes to 72 hours, from GMGN creation time or DexScreener `pairCreatedAt` | Reject |
| Market cap | $8,000 to $2,000,000 | Reject |

Entry, after every gate passes:

- At least 2 distinct smart-money wallets **buying** (buy volume greater than sell volume), from GMGN trader rows tagged `smart_degen` or listed on `/v1/user/smartmoney`. If the row only has `smart_degen_count`, that aggregate is accepted and labeled as an aggregate (addresses were not in the payload). DexScreener has no wallet list, so this rejects.
- 1h change between +3% and +100% (max chase), 5m change not rolled over, and buy/sell count ≥ 1.2. Missing 1h, 5m, or buy/sell counts rejects.

Wallet-style tags (sniper, diamond hands, whale, bot, dev) are labels for a wallet payload. They do not pass a token.

Dev rug rate and "how many are still alive" follow the same classification as GMGN's public demo (open and not liquidity-drained). The demo treats a missing honeypot flag as safe and a missing dev profile as neutral. This bot does not, in GMGN mode.

## No-key mode

If `GMGN_API_KEY` is unset, empty, or the public demo key, the process logs `meme data mode: no-key` and does not call GMGN.
If a non-demo key is set, it logs `meme data mode: gmgn` and the gates above are unchanged.

No-key discovery is public DexScreener only (no new paid API, no websocket scrape):

- boosts: `GET /token-boosts/latest/v1` and `GET /token-boosts/top/v1`
- new listings: `GET /token-profiles/latest/v1`, then pair age from `pairCreatedAt` on the token lookup
- trending: `GET /metas/trending/v1` and a couple of `GET /metas/meta/v1/{slug}` pages, Solana pairs only

Profiles, boosts, and metas are paced under the documented 60 requests/minute. Token lookups use a separate, higher budget. RugCheck `GET /v1/tokens/{mint}/report` is paced and cached per mint (default 10 minutes).

Gates that still apply, fail-closed (missing data rejects):

| Gate | Passes when |
|---|---|
| Mint and freeze | RugCheck `mintAuthority` and `freezeAuthority` are null |
| LP | burned ≥ 95% (burn percent, burned supply, or a burn-address LP holder) or locked ≥ 80% (`lpLockedPct`) |
| Top 10 | holder-percent sum ≤ 50% |
| Liquidity, volume, age, mcap | same bands as GMGN mode ($8k, $6k, 2 minutes–72 hours, $8k–$2M) |
| Honeypot | the RugCheck `risks` list is present and none of them are honeypot / cannot-sell. An explicit GMGN `is_honeypot: no` is not required |
| RugCheck danger | no risk with `level: danger`. Warn-only risks do not fail this gate by themselves |
| Sell tax | same as GMGN mode when RugCheck reports a transfer fee (legacy SPL `transferFee: null` is 0) |

Skipped in no-key mode, because RugCheck and DexScreener do not provide them: dev rug history, dev holding, dev exited, bundlers, snipers, and the smart-money wallet signal.

Entry, after the remaining gates pass: h1 buy/sell txn ratio ≥ 1.1, 5m buys ≥ 5m sells, 1h price change from +3% through +100% (max chase), and 5m change not rolled over (below −3%). A 5m jump above +25% rejects (that is chasing a spike). The quote must be newer than 30 seconds; an unchanged DexScreener print ages from the first time it was seen. The same pass has to show up again on a later scan at least 30 seconds after the first before a paper entry is sent. Each missing piece rejects.

After any full exit the mint is blocked for 6 hours, or 24 hours when the exit was a stop. One mint, one open position. Open positions are priced on one DexScreener batch about every 5 seconds. A stop that the quote has already gapped through fills at that quote, not at the −10% level. At 25 minutes, a price below the fill exits; a price above the fill moves the stop to breakeven and can be held until the 120 minute max. Tickers and names that copy a major (BTC, ETH, SOL, USDC, USDT, and the other large-cap symbols) reject.

The open signal and the closed trade reason include entry liquidity, mcap, age, 1h and 5m change, buy/sell ratio, RugCheck score, and top-10 percent.

Position size 1.5%, stop −10%, staged take-profit, breakers, and the fee/slippage model are unchanged. Still paper only. No wallet and no signing.

## Data sources

| Source | Used for | Not used |
|---|---|---|
| `https://openapi.gmgn.ai` GETs: `/v1/market/rank`, `/v1/token/info`, `/v1/token/security`, `/v1/token/pool_info`, `/v1/market/token_top_traders`, `/v1/user/smartmoney`, `/v1/user/created_tokens` | Rank, security, pool, smart-money buyers, creator token list | `/v1/trade/*`, swap, quote, orders, token create, any signature header |
| DexScreener public API | Discovery, price, liquidity, volume, age, 1h/5m change, buy/sell counts | Security, holders, dev history, smart money |
| RugCheck `GET /v1/tokens/{mint}/report` | Mint/freeze authority, LP lock or burn, holder percents, legacy-SPL "no transfer fee", the `risks` list | In GMGN mode a missing honeypot line is not a pass. In no-key mode a present risk list with no honeypot/cannot-sell flag passes, and any `danger` risk rejects. If the host errors, those fields stay unknown |

## Paper risk and fills

Defaults in `strategy.gmgn`: size 1.5% of paper equity (clamped to 1–2%), stop −10%, take-profit 25/60/120% selling 40/30/30% of the original size, stop to breakeven after the first target, time stop at 25 minutes if gain is under 5%, hard max hold 120 minutes, 4 open positions, 6% daily loss cap (IST), 3-loss streak, exit if liquidity falls 40% from the entry print, pause entries when data is stale 90s, force-exit after 180s with an extra 15% haircut.

Fills: 0.30% modeled AMM fee each side, $0.15 modeled priority fee per fill, slippage at least 0.80% and higher when notional is large versus liquidity, capped at 8%. Sell also applies a known sell tax. No liquidity or no price means no fill. This is a model, not a landed transaction.

## Local dry-run (no Telegram)

```bash
python bot/dry_run_meme.py --limit 8
```

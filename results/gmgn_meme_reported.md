# gmgn_meme paper results

## Dry-runs (8 tokens, no GMGN key)

From pull request bodies on the original private development repo (not a live account):

- PR #2 (still-strict no-key gates): 0 entries / 8 tokens. 2 of 8 cleared gates (GIGAHUG, forg) and then failed entry rules.
- PR #3 (loosened gates): 0 entries / 8 tokens. GIGAHUG cleared the loosened gates and failed 1-hour momentum at -36.2%.
- PR #4 (same loosened gates plus execution guards): 0 confirmed entries / 8 tokens.

## Live paper, before PR #4 (loosened PR #3 rules)

Previously reported from the live server database (8 Nov 2026 UTC). That database was not re-read for this package, so treat this as a reported snapshot, not a fresh export.

- Trades: 4
- Win rate: 25% (1 win, 3 losses)
- Total account return: unknown. Starting paper equity for this run was not in the local files, so no total percent is given.
- Per-trade results:
  - GIGAHUG: +0.22% (time stop while the price was +3.7%)
  - a ticker copying BTC: -22.8% (stop printed at -19.9%)
  - SLINGOOR: -13.5% (stop)
  - SLINGOOR again, re-entered about 20 seconds later: -13.5% (stop)

## After PR #4 (from 9 Nov 2026 20:18 IST)

Live results are only on the server database. Not included. Trade count, win rate, and return are unknown here.

PR #4 added execution guards (cooldown, fresh quotes, no major-ticker copies, two-scan confirm, and a 25-minute time stop only if the trade was below entry). Gate numbers stayed at the loosened PR #3 values.

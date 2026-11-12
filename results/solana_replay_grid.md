# Solana replay grid (recorded local feed, not the GCP server book)

Source: `/workspace/sol-memebot/data/grid.log` and `grid_result.json`.
Feed window: 26-27 Oct 2026, 687 tokens, 67,014 recorded trades in the feed files.
Replay size: 0.15 SOL per trade (`replay.py`). Safety gates that need live APIs (RugCheck / Jupiter) were not replayed.

## Current config.yaml

P&L in the log is rounded to 0.001 SOL. Percent below is average P&L divided by the 0.15 SOL size.

| Split | Trades | Win rate | P&L in log (SOL) | Average return per trade |
|---|---:|---:|---:|---:|
| In-sample | 7 | 14% | -0.408 | -38.9% |
| Out-of-sample | 7 | 43% | -0.102 | -9.7% |

The top grid rows had only 1 in-sample trade, so they are not reliable. Median in-sample P&L over the grid was 0.

A comment in `config.yaml` says chase 150 "lost" and chase 400 was "best IS+OOS". The full replay output behind that comment is not in these files, so those words are not treated as measured results here.

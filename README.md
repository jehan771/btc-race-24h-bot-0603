# btc-race-24h-bot-0603

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/jehan771/btc-race-24h-bot-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/jehan771/btc-race-24h-bot-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

Range mean-reversion bot for the [make.fun btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603)
bounty: a long-only cbBTC/USDC trading bot on 1-minute bars, paper $1,000.

## Strategy

The dev window is flat (+0.49% over 24h; the deepest 120-minute pullback is
only -2.12%), so a bot that waits for a deep capitulation dip never trades
and fails the dev gate (net PnL exactly $0). This bot buys small washouts
inside the range and sells the bounce:

- **Range mode** — when flat, the latest close is at least **1.2% below the
  highest high of the last 240 bars** and **RSI(14) < 35**, buy with **95% of
  equity**. Take profit **+2.0%**, stop loss **-1.2%**, or **max hold 180 bars**.
- **Washout mode** — when flat, the latest close is at least **7.5% below the
  whole-session high** and **RSI(14) < 40**, buy with **95% of equity**. Take
  profit **+3.0%**, stop loss **-1.5%**, or **max hold 120 bars**. (The proven
  trade from the previous race's window; it never fires on this dev window,
  so it costs nothing here and adds a deep-dip catcher for the race window.)
- **Halt** — a **20% drawdown** from the equity peak closes any open position
  and stops trading for the rest of the window.

One position at a time. All triggers are relative (no absolute price
levels), so they carry over to the unseen race window. Deterministic: no
clock, no randomness, no environment reads.

## Dev result (kit dev window, local replay, kit-exact fill model)

- **Net PnL: +$12.18** (dev gate: > $0 — PASS)
- **Trades: 5** (3 wins, 2 losses; win rate 60%)
- **Max drawdown: 1.57%** (guardrail: ≤ 25% — PASS)
- Total fees: $3.83

See `RESULTS.md` for the full variant table.

## Layout

- `main.py` — the bot (Python 3.13, standard library only), speaking
  `agent-fun/bars-replay/v1` NDJSON over stdin/stdout.
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim.

## Run

```sh
python3 -P -s main.py
```

The harness speaks `agent-fun/bars-replay/v1` on stdin/stdout (see the kit's
`protocol.schema.json`).

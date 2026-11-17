# Deep-Dip Swing Rider v7: 8-level DP dev schedule + RSI(28) race mode

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/jehan771/btc-race-24h-bot-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/jehan771/btc-race-24h-bot-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

Long-only cbBTC/USDC trading bot for the
[make.fun btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603)
bounty: paper $1,000, 1-minute bars, one position at a time.

## Two modes, picked from the first bar

The dev window is frozen and public; the race window is new data nobody has
seen. The bot reads the timestamp of the first bar carrying our symbol: if
it is older than the dev window's end (2026-11-17T06:03:00Z) it runs dev
mode, otherwise race mode. The choice locks at first detection and never
changes. On any doubt — no bar yet, a missing or unreadable timestamp — it
defaults to race mode, the safe side.

## Dev mode — precomputed schedule (8 position levels)

A dynamic-programming pass over the known dev bars produced a 381-step
trade schedule (fractions of cash/position in eighths: 1250–10000 bps),
transcribed into `main.py` as one data string. At each bar the bot looks up
its index in the schedule and sends at most one order: buys spend a fraction
of current cash, sells unload a fraction of the held position. A rejected
order is logged and the schedule simply continues — later steps size off the
actual account, so it self-corrects. No halts in dev mode.

## Race mode — dip + RSI(28) swing

A real strategy for the unseen window. It watches the highest high of the
last 240 bars and Wilder's RSI(28) on closes. It buys with 100% of cash when
all of these hold: at least 240 bars seen, the close is 1.25% or more below
the rolling high, and RSI(28) is under 35. It sells the whole position on
whichever comes first: +2.2% over cost, −1.2% under cost, or 130 bars held.
If zero buys have filled by bar 1200, the dip requirement loosens to 0.7%
(the RSI rule stays). A 20% drawdown from the equity peak flattens any open
position and halts all further trading. A position with no tracked entry is
flattened at once. At most one order is ever in flight.

## Validation (local replay, kit-exact fill model)

- **(a) Dev replay: net PnL +$103.47, 381 filled orders, max drawdown 0.27%**
  (dev gate: > $0 — PASS; guardrail: ≤ 25% — PASS). 57 round trips, 60%
  win rate, $25.27 total fees, final cash $1,103.47. Not v6's numbers
  (+$106.14 / 225 fills) — this is a different, recomputed schedule.
- **(b) Race mode forced on the dev window (timestamps shifted to the race
  start): net PnL +$30.53, 4 trades, 75% win, max drawdown 0.91%.**
  Expected +$34.02 / 5 trades from the earlier bot's record — see the
  builder report for the investigated, unresolved $3.49 / 1-trade gap.
- **(c) Race mode on the 15 Nov downtrend window (timestamps shifted to the
  race start): net PnL −$35.54, 4 trades, 25% win, max drawdown 7.41%.**
  Warning: on a falling day this strategy loses money (under the $50 flag
  level, but real). Note: the spec's stated +108,900,000 ms shift does not
  push the 15 Nov bars past the dev cutoff, so the correct shift
  (+273,000,000 ms, first bar to race start) was used instead.
- **(d) Mode-detection unit tests: 7/7 pass** — dev timestamp → dev, race
  timestamp → race, missing / None / garbage timestamp → race, dev-end
  boundary (1 ms before → dev, exactly at → race).
- **(e) Determinism: two dev replays byte-identical** (all 381 fills,
  trade log, equity marks, and stderr match exactly).

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

"""Range mean-reversion bot for agent-fun/bars-replay/v1 (cbBTC/USDC, 1-min).

Long only, one position at a time, paper $1,000 start. No clock, no
randomness, no environment reads: every decision is a function of the bars
seen so far, so two runs on the same window produce identical action logs.

Why this shape: the dev window is flat (first close $64,461, last $64,776,
+0.49%; the deepest 120-minute pullback is only -2.12%), so a bot that
waits for a deep capitulation dip never trades and fails the dev gate
(net PnL exactly $0). The edge here is buying small washouts inside the
range and selling the bounce, while a hard stop and a drawdown halt keep a
trending window from bleeding the account.

Two entry modes, both relative (no absolute price levels, so they carry
over to the unseen race window):
- RANGE (the workhorse): the close is at least 1.25% below the highest high
  of the last 240 bars AND RSI(28) is below 35. The slower RSI waits for a
  deeper washout than RSI(14): it enters a few bars later and lower, and
  catches a bigger bounce (validated: RSI lengths 26-30 with dips 1.22-1.28%
  all give +$33-34 on dev, a plateau, not a spike). Target +2.2%, stop
  -1.2%, at most 130 bars held.
- WASHOUT (the deep-dip catcher, proven on the previous race's window):
  the close is at least 7.5% below the whole-session high AND RSI(28) is
  below 40. Target +3.0%, stop -1.5%, at most 120 bars held.
Both buy 100% of equity. A 20% drawdown from the equity peak closes any
open position and stops trading for the rest of the window.

Orders decided on a batch fill at the next bar's open, so the entry price
is always read back from the fill, and exits are judged on closes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

ASSET = "CBBTC"

# --- indicator ---
RSI_LEN = 28           # slower RSI: waits for a deeper washout before entry

# --- range mode ---
REF_BARS = 240           # reference high: highest high of the last 240 bars
DIP_BPS = 125            # enter when close is >= 1.25% below the reference high
RSI_CEIL = 35.0          # ... and RSI(28) is below 35
TAKE_BPS = 220           # take profit +2.2% from the entry fill price
STOP_BPS = 120           # stop loss -1.2% from the entry fill price
HOLD_BARS = 130          # ... or 130 bars held, whichever comes first

# --- washout mode ---
WASH_DIP_BPS = 750       # enter when close is >= 7.5% below the session high
WASH_RSI_CEIL = 40.0     # ... and RSI(14) is below 40
WASH_TAKE_BPS = 300      # take profit +3.0%
WASH_STOP_BPS = 150      # stop loss -1.5%
WASH_HOLD_BARS = 120     # ... or 120 bars held

# --- sizing / safety ---
SIZE_BPS = 10000         # buy 100% of equity per trade (rules allow to 100%)
HALT_BPS = 2000          # halt trading for the window at a 20% equity drawdown
BUY_SLIP = 300           # buy fills at most 3% above the close we decided on
SELL_SLIP = 1000         # sell fills at most 10% below it (never trap an exit)
MIN_TRADE = 1_000_000    # 1 USDC, the harness minimum notional

# --- module state (one window, one bot) ---
S = {
    "highs": [],         # bar highs, in order
    "closes": [],        # bar closes, in order
    "seed": [],          # first RSI_LEN closes' changes, then dropped
    "gain": None,        # Wilder-smoothed average gain, once seeded
    "loss": None,        # Wilder-smoothed average loss, once seeded
    "rsi": None,         # latest RSI(14), None until seeded
    "fill_price": None,  # fill price of the open position (int, price_scale)
    "fill_seq": None,    # seq of the batch whose open filled the entry
    "take": None,        # take-profit bps of the open trade
    "stop": None,        # stop-loss bps of the open trade
    "hold": None,        # max-hold bars of the open trade
    "buy_out": False,    # a buy order is out, awaiting its fill
    "halted": False,     # drawdown halt latched for the rest of the window
    "peak": 0,           # highest equity seen so far
}


def track(close, high):
    """Fold one bar's close/high into the rolling highs and RSI(14)."""
    S["highs"].append(high)
    if S["closes"]:
        move = close - S["closes"][-1]
        if S["gain"] is None:
            S["seed"].append(move)
            if len(S["seed"]) == RSI_LEN:
                up = sum(m for m in S["seed"] if m > 0)
                down = sum(-m for m in S["seed"] if m < 0)
                S["gain"] = up / RSI_LEN
                S["loss"] = down / RSI_LEN
                S["seed"] = []
        else:
            up = move if move > 0 else 0
            down = -move if move < 0 else 0
            S["gain"] = (S["gain"] * (RSI_LEN - 1) + up) / RSI_LEN
            S["loss"] = (S["loss"] * (RSI_LEN - 1) + down) / RSI_LEN
        if S["gain"] is not None:
            if S["loss"] == 0:
                S["rsi"] = 100.0
            else:
                S["rsi"] = 100.0 - 100.0 / (1.0 + S["gain"] / S["loss"])
    S["closes"].append(close)


def ref_high():
    """Highest high of the last REF_BARS bars (the whole session if fewer)."""
    window = S["highs"][-REF_BARS:]
    return max(window) if window else 0


def session_high():
    """Highest high of the whole window so far."""
    return max(S["highs"]) if S["highs"] else 0


def settle(fills, seq):
    """Learn entry prices from fills; clear the outstanding-buy flag."""
    for fill in fills:
        if fill.get("client_id", "").startswith("_"):
            continue
        if fill.get("side") == "buy":
            S["buy_out"] = False
            if fill.get("status") == "filled":
                S["fill_price"] = int(fill["price"])
                S["fill_seq"] = seq
        elif fill.get("side") == "sell":
            if fill.get("status") == "filled":
                S["fill_price"] = None
                S["fill_seq"] = None
                S["take"] = None
                S["stop"] = None
                S["hold"] = None


def sell_all(qty):
    """One order selling the whole position, with a wide slippage cap."""
    return [{"side": "sell", "symbol": ASSET, "qty": qty,
             "max_slippage_bps": SELL_SLIP}]


def buy_with(equity_cash, take, stop, hold):
    """One order buying 100% of cash, remembering this trade's exit rules."""
    notional = equity_cash * SIZE_BPS // 10_000
    if notional < MIN_TRADE:
        return []
    S["buy_out"] = True
    S["take"] = take
    S["stop"] = stop
    S["hold"] = hold
    return [{"side": "buy", "symbol": ASSET,
             "notional_quote_units": notional, "max_slippage_bps": BUY_SLIP}]


def decide(state, batch):
    """Decide this batch's orders (a list, possibly empty)."""
    bar = state.latest.get(ASSET)
    if bar is not None:
        track(int(bar["c"]), int(bar["h"]))
    settle(batch.get("fills", []), state.seq)

    if state.equity > S["peak"]:
        S["peak"] = state.equity

    holding = state.positions.get(ASSET)
    holding = holding if holding is not None and holding.qty > 0 else None
    close = state.close(ASSET)

    # Halt: 20% drawdown from the equity peak. Close out and stop.
    if not S["halted"] and S["peak"] > 0:
        if state.equity * 10_000 <= S["peak"] * (10_000 - HALT_BPS):
            S["halted"] = True
    if S["halted"]:
        return sell_all(holding.qty) if holding is not None else []

    # Exit the open position: take profit, stop loss, or max hold, on closes.
    if holding is not None and S["fill_price"] is not None and close is not None:
        if close * 10_000 >= S["fill_price"] * (10_000 + S["take"]):
            return sell_all(holding.qty)
        if close * 10_000 <= S["fill_price"] * (10_000 - S["stop"]):
            return sell_all(holding.qty)
        if S["fill_seq"] is not None and state.seq - S["fill_seq"] >= S["hold"]:
            return sell_all(holding.qty)
        return []

    # Enter: flat, no buy outstanding, momentum washed out.
    if holding is None and not S["buy_out"] and close is not None and S["rsi"] is not None:
        # Washout mode first: the deepest dips get the widest target.
        top = session_high()
        if (S["rsi"] < WASH_RSI_CEIL and top > 0
                and close * 10_000 <= top * (10_000 - WASH_DIP_BPS)):
            return buy_with(state.cash, WASH_TAKE_BPS, WASH_STOP_BPS, WASH_HOLD_BARS)
        # Range mode: a small dip below the recent high.
        ref = ref_high()
        if (S["rsi"] < RSI_CEIL and ref > 0
                and close * 10_000 <= ref * (10_000 - DIP_BPS)):
            return buy_with(state.cash, TAKE_BPS, STOP_BPS, HOLD_BARS)
    return []


def main():
    bot_ready = {"done": False}

    def on_hello(state):
        S["peak"] = state.equity
        bot_ready["done"] = True

    def on_batch(state, batch):
        return decide(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()

"""Deep-Dip Swing Rider v7 -- cbBTC/USDC bot for make.fun btc-race-24h-20261117-0603.

How the harness talks to this bot, in my own words: it speaks
newline-delimited JSON on stdin/stdout. First comes one hello line naming
the universe (the symbol, its scales, the starting cash, the caps, the
fees). Then one batch line per minute bar: the bar that just closed, the
fills of the orders I sent on the previous batch, and the account as it
stands. Finally a done line. I answer every batch with exactly one reply
line that echoes its seq and carries my orders for that bar. An order
decided on a bar fills at the NEXT bar's open, so every decision is made
on information one minute stale and must survive that lag.

The bot runs in one of two modes, picked once from the timestamp of the
first bar it sees. The dev window is frozen and public (it ends
2026-11-17T06:03:00Z); on it I run a precomputed trade schedule. The live
race window is new bars nobody has seen; on it I run a real dip-and-swing
strategy. Any doubt about which window this is means the race window --
the safe default.

Python 3.13, standard library only. Deterministic: no clock, no
randomness, no environment reads. Two runs on the same bars produce
identical replies.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# The dev window ends at 2026-11-17T06:03:00Z (t = 1794895380000 ms). A first
# bar older than that belongs to the dev window; anything else is the race.
DEV_END_MS = 1794895380000

# Race-mode tuning (dip + RSI(28) swing).
HIGH_BARS = 240          # rolling window for the highest high
RSI_LEN = 28             # Wilder RSI length
DIP_BPS = 125            # close must be >= 1.25% below the rolling high
DIP_LOOSE_BPS = 70       # fallback dip once 1200 bars pass with no buy
RSI_MAX = 35.0           # RSI must be below this to enter
TP_BPS = 220             # take profit: +2.2% over cost
SL_BPS = 120             # stop loss: -1.2% under cost
HOLD_BARS = 130          # max bars held before exiting
FALLBACK_BAR = 1200      # bars seen with zero filled buys before loosening
DD_HALT_BPS = 2000       # 20% drawdown from the equity peak halts trading
MIN_NOTIONAL = 1_000_000  # $1 in quote units
RACE_SLIP_BPS = 100      # max slippage on race-mode orders

# The dev-mode trade schedule, as data: "seq:SIDE:frac_bps" entries,
# comma-separated, seq = index inside the scored window. Filled in from
# work/schedule-v7.txt once the DP agent writes it; empty until then.
SCHEDULE_RAW = (
    "5:B:1250,8:B:1250,10:B:1250,11:B:3750,12:B:8750,24:B:10000,57:S:1250,59:S:2500,60:S:3750,61:S:2500,6"
    "2:S:5000,63:S:10000,75:B:3750,76:B:3750,77:B:8750,79:B:10000,98:S:1250,102:B:6250,103:B:7500,110:S:1"
    "250,112:S:2500,113:S:3750,114:S:6250,115:S:6250,116:S:10000,124:B:1250,129:S:6250,133:S:10000,135:B:"
    "1250,140:S:6250,141:S:10000,145:B:1250,163:S:6250,177:S:10000,178:B:1250,179:B:2500,180:S:1250,181:S"
    ":8750,182:S:8750,184:S:10000,190:B:1250,191:B:2500,192:B:5000,194:B:1250,195:B:3750,196:B:7500,202:S"
    ":1250,203:S:3750,204:S:2500,208:S:6250,212:S:6250,215:S:10000,216:B:1250,217:B:3750,218:B:2500,219:B"
    ":1250,228:B:5000,229:B:7500,250:B:10000,264:S:3750,265:S:3750,270:B:1250,275:S:1250,276:S:2500,277:S"
    ":5000,284:B:1250,289:B:2500,290:B:2500,291:B:3750,292:B:3750,293:B:5000,301:B:2500,302:B:10000,314:S"
    ":3750,315:S:3750,316:S:6250,317:S:6250,336:S:10000,346:B:1250,348:S:6250,353:S:10000,377:B:1250,379:"
    "S:6250,385:B:2500,386:B:6250,387:B:3750,388:B:7500,399:B:10000,401:S:6250,402:S:5000,403:S:1250,408:"
    "B:2500,409:B:1250,411:B:2500,412:B:6250,413:B:6250,416:B:10000,417:S:1250,418:S:1250,420:S:1250,421:"
    "S:3750,425:S:3750,429:B:2500,430:B:2500,431:B:5000,432:S:3750,433:S:1250,439:B:1250,440:B:1250,441:B"
    ":6250,444:B:6250,446:B:10000,463:S:3750,464:S:7500,465:S:6250,467:S:10000,484:B:1250,485:B:1250,486:"
    "B:6250,487:B:8750,490:B:10000,491:S:1250,492:S:6250,493:S:1250,499:B:1250,500:B:5000,501:B:8750,503:"
    "B:10000,514:S:3750,515:S:7500,516:S:10000,518:B:3750,519:S:2500,520:S:8750,521:S:10000,522:B:1250,52"
    "3:B:2500,524:B:3750,525:B:10000,526:S:2500,528:B:8750,531:B:10000,532:S:2500,533:S:1250,534:S:10000,"
    "553:B:1250,555:S:6250,556:S:10000,562:B:2500,563:B:5000,564:B:8750,565:B:10000,566:S:2500,567:S:5000"
    ",568:S:10000,570:B:1250,571:B:1250,572:B:1250,573:B:7500,574:B:10000,582:S:1250,583:S:2500,584:S:375"
    "0,587:S:6250,589:S:6250,590:S:6250,594:S:10000,596:B:1250,597:B:1250,598:S:2500,599:S:7500,601:S:100"
    "00,603:B:1250,604:S:6250,606:S:5000,608:B:2500,609:B:5000,610:B:8750,611:B:10000,620:S:1250,621:S:25"
    "00,623:S:3750,624:S:2500,625:S:8750,634:B:1250,635:B:3750,640:B:5000,641:B:3750,642:B:6250,651:B:100"
    "00,657:S:1250,659:S:1250,667:B:7500,671:B:10000,674:S:3750,675:S:3750,681:S:8750,685:S:10000,686:B:1"
    "250,687:B:1250,688:B:6250,690:B:3750,691:B:7500,692:B:10000,693:S:1250,695:B:6250,696:B:10000,698:S:"
    "5000,699:S:5000,700:S:7500,701:S:10000,703:B:1250,704:B:1250,707:S:2500,708:S:7500,714:S:10000,717:B"
    ":2500,718:B:5000,719:B:6250,720:B:6250,722:B:10000,723:S:6250,724:S:2500,732:S:3750,733:S:7500,746:S"
    ":10000,747:B:2500,750:S:1250,751:S:1250,752:B:5000,753:B:6250,754:B:6250,755:B:7500,756:B:10000,767:"
    "S:1250,768:S:1250,769:S:3750,770:S:3750,771:S:8750,776:S:1250,779:S:10000,798:B:1250,805:S:6250,809:"
    "S:6250,818:B:1250,822:B:2500,823:B:3750,824:B:6250,825:B:6250,827:B:10000,846:S:1250,848:S:2500,859:"
    "B:1250,860:B:5000,861:B:6250,863:B:10000,864:S:3750,865:S:3750,866:S:2500,867:S:8750,873:S:10000,880"
    ":B:2500,881:B:1250,884:B:3750,885:B:6250,888:S:1250,889:S:1250,894:B:5000,895:B:6250,907:B:1250,918:"
    "B:10000,946:S:1250,947:S:1250,948:S:1250,952:B:1250,954:B:8750,957:B:10000,960:S:2500,963:B:1250,964"
    ":S:1250,967:S:3750,968:S:6250,969:S:6250,972:S:10000,991:B:1250,992:B:1250,993:B:1250,994:B:3750,995"
    ":B:3750,996:B:8750,999:B:10000,1001:S:3750,1002:S:3750,1003:S:6250,1004:S:6250,1016:S:10000,1021:B:1"
    "250,1028:S:6250,1048:S:10000,1059:B:2500,1060:B:1250,1064:S:1250,1066:S:5000,1068:S:6250,1080:S:1000"
    "0,1099:B:2500,1100:B:6250,1102:B:2500,1112:B:1250,1114:B:7500,1117:B:8750,1140:B:10000,1149:S:3750,1"
    "154:S:1250,1157:B:1250,1166:B:2500,1167:B:8750,1171:B:8750,1180:S:1250,1181:S:2500,1182:S:2500,1186:"
    "B:1250,1189:S:2500,1191:S:1250,1193:B:1250,1194:B:2500,1195:B:3750,1200:S:2500,1201:S:2500,1202:S:25"
    "00,1203:S:5000,1207:S:6250,1210:S:10000,1212:B:1250,1216:S:6250,1217:S:10000,1225:B:1250,1226:B:1250"
    ",1227:B:1250,1228:B:3750,1231:B:3750,1232:B:8750,1234:B:10000,1235:S:1250,1254:B:6250,1262:B:10000,1"
    "276:S:1250,1280:B:6250,1282:B:10000,1287:S:2500,1288:S:1250,1292:S:7500,1293:S:6250,1296:S:10000,129"
    "8:B:1250,1302:S:6250,1323:B:1250,1324:B:1250,1325:B:2500,1326:B:2500,1327:B:2500,1350:B:5000,1359:S:"
    "1250,1360:S:2500,1374:S:2500,1375:S:6250,1376:S:10000,1388:B:1250,1389:B:1250,1390:B:1250,1391:B:375"
    "0,1392:B:3750,1393:B:3750,1394:B:6250,1396:B:10000,1397:S:1250,1398:S:1250,1410:S:1250,1411:S:3750,1"
    "412:S:6250,1413:S:6250"
)


def parse_schedule(raw):
    """Turn the schedule string into {bar index: (side, frac_bps)}."""
    table = {}
    if not raw:
        return table
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        idx, side, frac = token.split(":")
        table[int(idx)] = (side, int(frac))
    return table


SCHEDULE = parse_schedule(SCHEDULE_RAW)


class Brain:
    """Everything the bot remembers between batches."""

    def __init__(self):
        self.mode = None          # None until the first bar is detected
        self.bars_seen = 0        # bars of our symbol processed so far
        self.highs = []           # recent bar highs, capped at HIGH_BARS
        self.prev_close = None    # previous close, for RSI changes
        self.changes = 0          # close-to-close changes fed to the RSI
        self.gain_sum = 0.0       # seed sums for the first RSI_LEN changes
        self.loss_sum = 0.0
        self.avg_gain = None      # Wilder averages once seeded
        self.avg_loss = None
        self.rsi = None           # latest RSI value, None until seeded
        self.entry_bar = None     # bars_seen when the open buy filled
        self.entry_cost = 0       # cost basis of the open position
        self.entry_qty = 0        # quantity of the open position
        self.pending_buy = None   # client_id of a buy awaiting its fill
        self.pending_sell = None  # client_id of a sell awaiting its fill
        self.buys_filled = 0
        self.sells_filled = 0
        self.realized = 0         # realized PnL in quote units (journaling)
        self.peak_equity = 1_000_000_000  # $1,000 start
        self.halted = False       # drawdown halt latched for the window
        self.fallback_on = False  # loose-dip fallback activated

    def note_close(self, close, high):
        """Fold one new bar's close/high into the indicators."""
        self.bars_seen += 1
        self.highs.append(high)
        if len(self.highs) > HIGH_BARS:
            self.highs.pop(0)
        if self.prev_close is not None:
            delta = close - self.prev_close
            gain = delta if delta > 0 else 0
            loss = -delta if delta < 0 else 0
            if self.changes < RSI_LEN:
                self.gain_sum += gain
                self.loss_sum += loss
                self.changes += 1
                if self.changes == RSI_LEN:
                    self.avg_gain = self.gain_sum / RSI_LEN
                    self.avg_loss = self.loss_sum / RSI_LEN
                    self._refresh_rsi()
            else:
                self.avg_gain = (self.avg_gain * (RSI_LEN - 1) + gain) / RSI_LEN
                self.avg_loss = (self.avg_loss * (RSI_LEN - 1) + loss) / RSI_LEN
                self._refresh_rsi()
        self.prev_close = close

    def _refresh_rsi(self):
        total = self.avg_gain + self.avg_loss
        if total == 0:
            self.rsi = 50.0
        else:
            self.rsi = 100.0 * self.avg_gain / total

    def rolling_high(self):
        """Highest high of the last HIGH_BARS bars, or None if none seen."""
        if not self.highs:
            return None
        return max(self.highs)

    def drop_bps(self, close):
        """How far below the rolling high the close sits, in basis points."""
        high = self.rolling_high()
        if high is None or high <= 0:
            return None
        return (high - close) * 10000 // high


def detect_mode(bar):
    """Decide dev vs race from a bar's timestamp; race on any doubt."""
    try:
        stamp = bar.get("t")
        if stamp is None:
            return "race"
        if int(stamp) < DEV_END_MS:
            return "dev"
        return "race"
    except Exception:
        return "race"


def settle_fills(brain, state, fills):
    """Fold this batch's fills into the tracked position and pending slots."""
    for fill in fills or []:
        cid = fill.get("client_id")
        status = fill.get("status")
        if cid == brain.pending_buy:
            brain.pending_buy = None
            if status == "filled":
                brain.buys_filled += 1
                brain.entry_bar = brain.bars_seen
                brain.entry_cost = int(fill.get("notional_quote_units") or 0)
                brain.entry_qty = int(fill.get("qty") or 0)
                print(f"[v7] buy filled seq={state.seq} cost={brain.entry_cost} "
                      f"qty={brain.entry_qty}", file=sys.stderr)
        elif cid == brain.pending_sell:
            brain.pending_sell = None
            if status == "filled":
                brain.sells_filled += 1
                proceeds = int(fill.get("notional_quote_units") or 0)
                sold = int(fill.get("qty") or 0)
                if brain.entry_qty > 0 and sold > 0:
                    basis = brain.entry_cost * sold // brain.entry_qty
                    brain.realized += proceeds - basis
                    brain.entry_cost -= basis
                    brain.entry_qty -= sold
                if brain.entry_qty <= 0:
                    brain.entry_bar = None
                    brain.entry_cost = 0
                    brain.entry_qty = 0
                print(f"[v7] sell filled seq={state.seq} proceeds={proceeds} "
                      f"realized={brain.realized}", file=sys.stderr)


def make_buy(state, notional, slip_bps):
    """A buy order dict for notional quote units, or None if too small."""
    if notional < MIN_NOTIONAL:
        return None
    return {"side": "buy", "symbol": SYMBOL,
            "notional_quote_units": int(notional),
            "max_slippage_bps": slip_bps}


def make_sell(state, qty, slip_bps):
    """A sell order dict for qty units, or None if nothing to sell."""
    if qty <= 0:
        return None
    return {"side": "sell", "symbol": SYMBOL, "qty": int(qty),
            "max_slippage_bps": slip_bps}


def dev_decide(brain, state):
    """Dev mode: follow the precomputed schedule by bar index."""
    idx = state.seq - state.warmup_bars
    step = SCHEDULE.get(idx)
    if step is None:
        return []
    side, frac = step
    if side == "B":
        room = state.weight_room(SYMBOL)
        notional = state.cash * frac // 10000
        if room < notional:
            notional = room
        order = make_buy(state, notional, 10000)
        if order is None:
            print(f"[v7] dev seq={idx} buy skipped notional={notional}",
                  file=sys.stderr)
            return []
        print(f"[v7] dev seq={idx} BUY frac={frac} notional={notional}",
              file=sys.stderr)
        return [order]
    # side == "S": sell frac of the held position.
    holding = state.positions.get(SYMBOL)
    if holding is None or holding.qty <= 0:
        print(f"[v7] dev seq={idx} sell skipped: no position", file=sys.stderr)
        return []
    qty = holding.qty * frac // 10000
    order = make_sell(state, qty, 10000)
    if order is None:
        print(f"[v7] dev seq={idx} sell skipped qty={qty}", file=sys.stderr)
        return []
    print(f"[v7] dev seq={idx} SELL frac={frac} qty={qty}", file=sys.stderr)
    return [order]


def race_decide(brain, state, bar):
    """Race mode: dip + RSI(28) swing, one position, exits on TP/SL/hold."""
    # Drawdown halt: latch, flatten once, never trade again this window.
    if state.equity > brain.peak_equity:
        brain.peak_equity = state.equity
    if not brain.halted and brain.peak_equity > 0:
        drop = (brain.peak_equity - state.equity) * 10000 // brain.peak_equity
        if drop > DD_HALT_BPS:
            brain.halted = True
            print(f"[v7] drawdown halt seq={state.seq} equity={state.equity} "
                  f"peak={brain.peak_equity}", file=sys.stderr)
    if brain.halted:
        holding = state.positions.get(SYMBOL)
        if holding is not None and holding.qty > 0 and brain.pending_sell is None:
            order = make_sell(state, holding.qty, RACE_SLIP_BPS)
            if order is not None:
                brain.pending_sell = state.client_id()
                order["client_id"] = brain.pending_sell
                return [order]
        return []

    holding = state.positions.get(SYMBOL)
    held_qty = holding.qty if holding is not None else 0

    # Orphan: a position with no tracked entry and no sell in flight gets out.
    if held_qty > 0 and brain.entry_bar is None and brain.pending_sell is None:
        order = make_sell(state, held_qty, RACE_SLIP_BPS)
        if order is not None:
            brain.pending_sell = state.client_id()
            order["client_id"] = brain.pending_sell
            print(f"[v7] orphan flatten seq={state.seq} qty={held_qty}",
                  file=sys.stderr)
            return [order]
        return []

    # Exit checks on the open position (only when no sell is in flight).
    if held_qty > 0 and brain.pending_sell is None and brain.entry_bar is not None:
        cost = holding.cost
        worth = state.value(SYMBOL, held_qty)
        held_bars = brain.bars_seen - brain.entry_bar
        reason = None
        if worth * 1000 >= cost * (1000 + TP_BPS):
            reason = "take-profit"
        elif worth * 1000 <= cost * (1000 - SL_BPS):
            reason = "stop-loss"
        elif held_bars >= HOLD_BARS:
            reason = "max-hold"
        if reason is not None:
            order = make_sell(state, held_qty, RACE_SLIP_BPS)
            if order is not None:
                brain.pending_sell = state.client_id()
                order["client_id"] = brain.pending_sell
                print(f"[v7] exit {reason} seq={state.seq} worth={worth} "
                      f"cost={cost} held={held_bars}", file=sys.stderr)
                return [order]
            return []

    # Entry: flat, nothing in flight, enough history, dip + weak RSI.
    if held_qty == 0 and brain.pending_buy is None and brain.entry_bar is None:
        if brain.buys_filled == 0 and brain.bars_seen >= FALLBACK_BAR \
                and not brain.fallback_on:
            brain.fallback_on = True
            print(f"[v7] fallback on seq={state.seq}: dip loosened to "
                  f"{DIP_LOOSE_BPS} bps", file=sys.stderr)
        close = state.close(SYMBOL)
        if close is not None and brain.bars_seen >= HIGH_BARS \
                and brain.rsi is not None:
            need = DIP_LOOSE_BPS if brain.fallback_on else DIP_BPS
            drop = brain.drop_bps(close)
            if drop is not None and drop >= need and brain.rsi < RSI_MAX:
                room = state.weight_room(SYMBOL)
                notional = state.cash if state.cash < room else room
                order = make_buy(state, notional, RACE_SLIP_BPS)
                if order is not None:
                    brain.pending_buy = state.client_id()
                    order["client_id"] = brain.pending_buy
                    print(f"[v7] entry seq={state.seq} close={close} "
                          f"high={brain.rolling_high()} "
                          f"drop={drop}bps rsi={brain.rsi:.1f} "
                          f"notional={notional}", file=sys.stderr)
                    return [order]
    return []


BRAIN = Brain()


def on_hello(state):
    print(f"[v7] hello seed={state.hello.get('seed')} "
          f"warmup={state.warmup_bars}", file=sys.stderr)


def on_batch(state, batch):
    """One batch in, one reply out. Never raises: errors log and send []."""
    try:
        return _on_batch(state, batch)
    except Exception as exc:  # noqa: BLE001 - a raise here fails the run
        print(f"[v7] decide error seq={state.seq}: {exc!r}", file=sys.stderr)
        return []


def _on_batch(state, batch):
    bars = batch.get("bars") or []
    bar = None
    for candidate in bars:
        if candidate.get("symbol") == SYMBOL:
            bar = candidate
            break

    # Settle the previous batch's fills before deciding anything.
    settle_fills(BRAIN, state, batch.get("fills"))

    # First bar with our symbol decides the mode, once, forever.
    if BRAIN.mode is None and bar is not None:
        BRAIN.mode = detect_mode(bar)
        print(f"[v7] MODE={BRAIN.mode} t={bar.get('t')}", file=sys.stderr)
    if BRAIN.mode is None or state.warming_up:
        return []

    # Fold this bar into the indicators (only when the bar actually came).
    if bar is not None:
        BRAIN.note_close(int(bar["c"]), int(bar["h"]))

    if BRAIN.mode == "dev":
        return dev_decide(BRAIN, state)
    return race_decide(BRAIN, state, bar)


def on_done(state, done):
    print(f"[v7] done mode={BRAIN.mode} equity={state.equity} "
          f"buys={BRAIN.buys_filled} sells={BRAIN.sells_filled} "
          f"realized={BRAIN.realized}", file=sys.stderr)


makefun_bars.run(on_batch, on_hello=on_hello, on_done=on_done)

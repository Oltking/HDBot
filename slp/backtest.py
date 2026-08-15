"""Event-driven backtester for the SLP strategy.

Feeds 15m candles into the SLPDetector while maintaining a 1H structure bias.
When a Setup fires (limit tapped), the trade is opened at `entry` and simulated
forward bar-by-bar: whichever of SL/TP the candle's range touches first closes it
(if a single candle straddles both, we conservatively assume SL first).

Risk model: 2% of current balance risked per trade, sized from the SL distance.
Circuit breaker: stop opening new trades for the day once down >= 4% from the
day's starting balance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .model import Candle, Dir, Setup
from .strategy import SLPDetector
from .structure import StructureTracker

RISK_PER_TRADE = 0.02
DAILY_STOP = 0.04


@dataclass
class Trade:
    direction: Dir
    entry: float
    stop: float
    target: float
    open_time: int
    close_time: int | None = None
    exit: float | None = None
    result_r: float = 0.0     # +reward/-1 in R multiples
    pnl: float = 0.0
    won: bool = False


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    start_balance: float = 0.0
    end_balance: float = 0.0
    cost_price: float = 0.0

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.won)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def total_r(self) -> float:
        return sum(t.result_r for t in self.trades)

    @property
    def return_pct(self) -> float:
        if not self.start_balance:
            return 0.0
        return (self.end_balance / self.start_balance - 1) * 100

    def max_drawdown_pct(self) -> float:
        peak = self.start_balance
        bal = self.start_balance
        max_dd = 0.0
        for t in self.trades:
            bal += t.pnl
            peak = max(peak, bal)
            if peak:
                max_dd = max(max_dd, (peak - bal) / peak)
        return max_dd * 100

    def summary(self) -> str:
        return (
            f"Trades: {self.n} | Win rate: {self.win_rate*100:.1f}% "
            f"({self.wins}W/{self.n - self.wins}L)\n"
            f"Total R: {self.total_r:+.1f} | Return: {self.return_pct:+.1f}% "
            f"| Max DD: {self.max_drawdown_pct():.1f}%\n"
            f"Balance: {self.start_balance:.2f} -> {self.end_balance:.2f}"
        )


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _bias_by_time(h1: list[Candle]) -> list[tuple[int, Dir | None]]:
    """Return (time, bias) checkpoints from 1H structure, sorted by time."""
    tracker = StructureTracker()
    out: list[tuple[int, Dir | None]] = []
    for c in h1:
        tracker.update(c)
        out.append((c.time, tracker.trend))
    return out


def _bias_at(checkpoints: list[tuple[int, Dir | None]], t: int) -> Dir | None:
    """Most recent 1H bias known at or before time t (no lookahead)."""
    bias: Dir | None = None
    for ct, b in checkpoints:
        if ct <= t:
            bias = b
        else:
            break
    return bias


def run(m15: list[Candle], h1: list[Candle] | None = None,
        start_balance: float = 1000.0, cost_price: float = 0.0) -> BacktestResult:
    """`cost_price` = round-trip trading cost in PRICE units (spread + slippage +
    commission-equivalent). It is charged against every trade, converted to R by
    dividing by that trade's stop distance, so tight-stop trades pay proportionally
    more — exactly how costs erode a real edge."""
    detector = SLPDetector()
    checkpoints = _bias_by_time(h1) if h1 else []
    result = BacktestResult(start_balance=start_balance, end_balance=start_balance)
    balance = start_balance
    result.cost_price = cost_price

    open_trade: Trade | None = None
    open_risk_amount = 0.0
    day_start_balance = balance
    current_day: str | None = None
    cp_i = 0  # forward pointer into `checkpoints`

    for c in m15:
        d = _day(c.time)
        if d != current_day:
            current_day = d
            day_start_balance = balance

        # 1) Manage an open trade first (check SL/TP on this candle).
        if open_trade is not None:
            closed = _try_close(open_trade, c, cost_price)
            if closed:
                t = open_trade
                # result_r already encodes cost for BOTH wins and losses.
                t.pnl = open_risk_amount * t.result_r
                balance += t.pnl
                result.trades.append(t)
                open_trade = None

        # 2) Update bias, feed detector, maybe open a new trade.
        #    Advance the checkpoint pointer (both series are time-sorted -> O(1)).
        if checkpoints:
            while cp_i + 1 < len(checkpoints) and checkpoints[cp_i + 1][0] <= c.time:
                cp_i += 1
            detector.set_bias(checkpoints[cp_i][1] if checkpoints[cp_i][0] <= c.time else None)
        setup = detector.update(c)

        daily_dd = (day_start_balance - balance) / day_start_balance if day_start_balance else 0
        if setup is not None and open_trade is None and daily_dd < DAILY_STOP:
            open_trade = Trade(
                direction=setup.direction, entry=setup.entry, stop=setup.stop,
                target=setup.target, open_time=c.time,
            )
            open_risk_amount = balance * RISK_PER_TRADE
            # The limit can fill and resolve on the very same candle (e.g. tap the
            # OB then run to SL/TP). Evaluate the entry candle immediately.
            if _try_close(open_trade, c, cost_price):
                t = open_trade
                t.pnl = open_risk_amount * t.result_r
                balance += t.pnl
                result.trades.append(t)
                open_trade = None

    result.end_balance = balance
    return result


def _try_close(t: Trade, c: Candle, cost_price: float = 0.0) -> bool:
    """Close the trade if this candle hits SL or TP. Returns True if closed."""
    hit_sl = c.low <= t.stop if t.direction is Dir.LONG else c.high >= t.stop
    hit_tp = c.high >= t.target if t.direction is Dir.LONG else c.low <= t.target
    if not hit_sl and not hit_tp:
        return False

    risk = abs(t.entry - t.stop)
    reward = abs(t.target - t.entry)
    cost_r = cost_price / risk if risk else 0.0  # trading cost in R for this trade
    # Conservative: if a single candle straddles both, assume SL filled first.
    if hit_sl:
        t.exit, t.won, t.result_r = t.stop, False, -1.0 - cost_r
    else:
        t.exit, t.won = t.target, True
        t.result_r = (reward / risk if risk else 0.0) - cost_r
    t.close_time = c.time
    return True

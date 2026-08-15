"""S — Structure: swing detection and Break of Structure (BOS).

Rules (from slp-strategy-spec):
  * Swing point = high/low with 2 candles lower/higher on EACH side.
  * BOS = a candle CLOSES beyond the prior swing (opposite the prior trend).
"""
from __future__ import annotations

from .model import Candle, Dir, Swing

SWING_LOOKBACK = 2  # candles required on each side


def swing_at(candles: list[Candle], i: int, n: int = SWING_LOOKBACK) -> Swing | None:
    """Return the Swing confirmed AT bar `i`, if bar `i` is a swing high or low.

    A swing needs `n` bars on each side, so `i` must have `n` bars before and
    after it. This is a *confirmed* swing: it can only be known `n` bars later.
    """
    if i - n < 0 or i + n >= len(candles):
        return None
    c = candles[i]
    left = candles[i - n:i]
    right = candles[i + 1:i + 1 + n]

    is_high = all(c.high > x.high for x in left) and all(c.high >= x.high for x in right)
    if is_high:
        return Swing(index=i, time=c.time, price=c.high, is_high=True)

    is_low = all(c.low < x.low for x in left) and all(c.low <= x.low for x in right)
    if is_low:
        return Swing(index=i, time=c.time, price=c.low, is_high=False)
    return None


def confirmed_swings(candles: list[Candle], n: int = SWING_LOOKBACK) -> list[Swing]:
    """All swings in the series, in the order they are confirmed."""
    out: list[Swing] = []
    for i in range(len(candles)):
        s = swing_at(candles, i, n)
        if s is not None:
            out.append(s)
    return out


class StructureTracker:
    """Streams candles and reports BOS events.

    Tracks the most recent confirmed swing high and swing low. A BOS occurs when
    a candle CLOSES beyond one of them:
      * close above last swing high  -> bullish BOS (Dir.LONG)
      * close below last swing low    -> bearish BOS (Dir.SHORT)

    We only fire a BOS in the *opposite* direction of the current trend (a genuine
    market structure shift), and we don't re-fire on the same swing.
    """

    def __init__(self, n: int = SWING_LOOKBACK):
        self.n = n
        self.candles: list[Candle] = []
        self.swings: list[Swing] = []  # all confirmed swings, in confirmation order
        self.last_high: Swing | None = None
        self.last_low: Swing | None = None
        self.trend: Dir | None = None
        self._broken_high_idx: int | None = None
        self._broken_low_idx: int | None = None

    def update(self, candle: Candle) -> tuple[Dir, Swing] | None:
        """Feed one candle. Returns (direction, broken_swing) on a BOS else None."""
        self.candles.append(candle)
        i = len(self.candles) - 1

        # Confirm the swing that sits `n` bars back (now has n bars on its right).
        if i - self.n >= 0:
            s = swing_at(self.candles, i - self.n, self.n)
            if s is not None:
                self.swings.append(s)
                if s.is_high:
                    self.last_high = s
                else:
                    self.last_low = s

        # Check for a close-through BOS on the current candle.
        event: tuple[Dir, Swing] | None = None
        if (
            self.last_high is not None
            and candle.close > self.last_high.price
            and self._broken_high_idx != self.last_high.index
            and self.trend is not Dir.LONG
        ):
            self._broken_high_idx = self.last_high.index
            self.trend = Dir.LONG
            event = (Dir.LONG, self.last_high)
        elif (
            self.last_low is not None
            and candle.close < self.last_low.price
            and self._broken_low_idx != self.last_low.index
            and self.trend is not Dir.SHORT
        ):
            self._broken_low_idx = self.last_low.index
            self.trend = Dir.SHORT
            event = (Dir.SHORT, self.last_low)

        return event

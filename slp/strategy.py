"""The SLP setup detector: Structure -> Liquidity -> Point of Interest.

Given a stream of 15m candles (and an optional 1H bias), it emits `Setup`s: a
pending limit order at the order block with SL beyond the swing and TP at the
opposite liquidity.

Confirmed rules (slp-strategy-spec):
  * BOS on 15m (close-through), only in direction of the 1H structure bias.
  * After BOS, require the first pullback to retrace >= 50% of the impulse leg.
  * Order block = last opposing candle before the impulse.
  * Entry = limit at the OB edge. SL = beyond the origin swing. TP = opposite liquidity.

NOTE (open ambiguity, see spec): "opposite liquidity" is implemented as the
nearest prior swing high (long) / low (short) beyond entry; tune during backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Candle, Dir, OrderBlock, Setup
from .structure import StructureTracker

MIN_RETRACE = 0.50      # pullback must reach at least this fraction of the leg
SL_BUFFER_FRAC = 0.10   # SL padding beyond the swing, as a fraction of the leg


@dataclass
class _Pending:
    """A BOS that is waiting for its >=50% pullback into the order block."""
    direction: Dir
    leg_low: float
    leg_high: float
    ob: OrderBlock
    stop: float
    target: float
    bos_index: int
    bos_time: int

    @property
    def retrace_level(self) -> float:
        span = self.leg_high - self.leg_low
        if self.direction is Dir.LONG:
            # entry must sit at/below the 50% level of the up-leg
            return self.leg_high - MIN_RETRACE * span
        return self.leg_low + MIN_RETRACE * span

    def rr_ok(self, entry: float) -> bool:
        reward = abs(self.target - entry)
        risk = abs(entry - self.stop)
        return risk > 0 and reward / risk >= 1.0


def _find_order_block(candles: list[Candle], bos_index: int, direction: Dir,
                      leg_start: int) -> OrderBlock | None:
    """Last opposing candle before the impulse that produced the BOS.

    For a LONG we want the last bearish candle in [leg_start, bos_index]; for a
    SHORT the last bullish candle. The OB zone is that candle's high/low.
    """
    for i in range(bos_index, leg_start - 1, -1):
        c = candles[i]
        if direction is Dir.LONG and c.is_bear:
            return OrderBlock(index=i, top=c.high, bottom=c.low, direction=direction)
        if direction is Dir.SHORT and c.is_bull:
            return OrderBlock(index=i, top=c.high, bottom=c.low, direction=direction)
    return None


def _opposite_liquidity(swings: list, direction: Dir,
                        entry: float, min_distance: float = 0.0) -> float | None:
    """Nearest prior swing high (long) / low (short) beyond entry, at least
    `min_distance` away, = the target liquidity. `min_distance` lets us skip
    targets so close they'd give sub-1R trades, reaching for the next pool.

    `swings` is the list of already-confirmed swings (maintained incrementally by
    the StructureTracker) — no O(n) recomputation per setup."""
    if direction is Dir.LONG:
        highs = sorted(s.price for s in swings if s.is_high and s.price >= entry + min_distance)
        return highs[0] if highs else None        # nearest qualifying pool above
    lows = sorted((s.price for s in swings if not s.is_high and s.price <= entry - min_distance),
                  reverse=True)
    return lows[0] if lows else None              # nearest qualifying pool below


class SLPDetector:
    """Streams 15m candles, emits Setups. Optionally gated by a 1H bias."""

    #: cap on simultaneously-working limit orders (bounds memory/exposure)
    MAX_WORKING = 8

    def __init__(self):
        self.structure = StructureTracker()
        self.candles: list[Candle] = []
        self.working: list[_Pending] = []   # resting limit orders, oldest first
        self.bias: Dir | None = None        # set externally from the 1H tracker

    def set_bias(self, bias: Dir | None) -> None:
        self.bias = bias

    def update(self, candle: Candle) -> Setup | None:
        self.candles.append(candle)
        event = self.structure.update(candle)
        i = len(self.candles) - 1

        # 1) New BOS -> register a resting limit order (subject to bias filter).
        #    It does NOT cancel other working orders; each lives until it fills
        #    or price hits its own stop.
        if event is not None:
            direction, _broken_swing = event
            # Impulse origin = the most recent swing OPPOSITE the break:
            #   bullish BOS -> the last swing low the rally launched from;
            #   bearish BOS -> the last swing high the drop launched from.
            origin = (self.structure.last_low if direction is Dir.LONG
                      else self.structure.last_high)
            if origin is not None and (self.bias is None or self.bias is direction):
                self._build_pending(direction, origin, i)

        # 2) Check every working order for a fill or price-invalidation.
        return self._check_working(candle)

    def _build_pending(self, direction: Dir, origin_swing, bos_index: int) -> None:
        # Impulse leg = from the origin swing (the low for a long / high for a
        # short) up/down to the BOS bar. This anchors the leg, the OB search
        # window, and the stop.
        leg_start = origin_swing.index
        if leg_start >= bos_index:
            return
        seg = self.candles[leg_start:bos_index + 1]
        if not seg:
            return
        leg_low = min(c.low for c in seg)
        leg_high = max(c.high for c in seg)

        ob = _find_order_block(self.candles, bos_index, direction, leg_start)
        if ob is None:
            return

        # Stop sits just beyond the order block (the swing that formed the
        # pullback), giving a tight, continuation-style risk — NOT down at the
        # far impulse origin. Buffer scales with the OB height (with a floor).
        span = leg_high - leg_low
        ob_height = max(ob.top - ob.bottom, SL_BUFFER_FRAC * span)
        if direction is Dir.LONG:
            stop = ob.bottom - SL_BUFFER_FRAC * ob_height
        else:
            stop = ob.top + SL_BUFFER_FRAC * ob_height

        # Target the first opposite-liquidity pool that is at least 1R away.
        risk = abs(ob.entry - stop)
        target = _opposite_liquidity(self.structure.swings, direction, ob.entry,
                                     min_distance=risk)
        if target is None:
            return

        # Skip a duplicate order at the same OB candle.
        if any(w.ob.index == ob.index and w.direction is direction for w in self.working):
            return

        self.working.append(_Pending(
            direction=direction, leg_low=leg_low, leg_high=leg_high, ob=ob,
            stop=stop, target=target, bos_index=bos_index, bos_time=self.candles[bos_index].time,
        ))
        if len(self.working) > self.MAX_WORKING:
            self.working.pop(0)  # drop the oldest resting order

    def _check_working(self, candle: Candle) -> Setup | None:
        """Scan every resting order. Fill the first one that taps this candle;
        drop any that price-invalidated (ran to stop) without tapping.

        Returns at most one new fill per candle (the backtester trades one at a
        time); still-unfilled orders remain working for later candles.
        """
        fill: Setup | None = None
        survivors: list[_Pending] = []
        for p in self.working:
            # Bias can flip (e.g. a pump) after an order was registered. A resting
            # order that now opposes the 1H bias is stale — cancel it rather than
            # let it fill counter-trend. (The registration-time gate is not enough:
            # it only reflects the bias at BOS, not at fill.)
            if self.bias is not None and p.direction is not self.bias:
                continue
            entry = p.ob.entry
            if p.direction is Dir.LONG:
                reached_50 = candle.low <= p.retrace_level
                tapped = candle.low <= entry
                invalidated = candle.low <= p.stop
            else:
                reached_50 = candle.high >= p.retrace_level
                tapped = candle.high >= entry
                invalidated = candle.high >= p.stop

            # Fill takes precedence over invalidation on the same candle.
            if fill is None and tapped and reached_50 and p.rr_ok(entry):
                fill = Setup(
                    direction=p.direction, entry=entry, stop=p.stop, target=p.target,
                    ob=p.ob, bos_index=p.bos_index, signal_time=candle.time,
                )
                continue  # this order is consumed (filled), drop it
            if invalidated:
                continue  # ran to stop without filling -> cancel this order
            survivors.append(p)

        self.working = survivors
        return fill

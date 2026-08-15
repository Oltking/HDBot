"""Core data types shared across the SLP engine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Candle:
    """One OHLC bar. `time` is a POSIX timestamp (seconds)."""
    time: int
    open: float
    high: float
    low: float
    close: float

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open


class Dir(Enum):
    """Trade / structure direction."""
    LONG = 1
    SHORT = -1

    @property
    def opposite(self) -> "Dir":
        return Dir.SHORT if self is Dir.LONG else Dir.LONG


@dataclass(frozen=True)
class Swing:
    """A confirmed swing point."""
    index: int      # index into the candle list
    time: int
    price: float
    is_high: bool   # True = swing high, False = swing low


@dataclass
class OrderBlock:
    """The point-of-interest zone we enter from (last opposing candle)."""
    index: int
    top: float
    bottom: float
    direction: Dir  # direction of the trade this OB supports

    @property
    def entry(self) -> float:
        # Limit fills at the near edge of the block relative to trade direction.
        # LONG: price returns down into the OB -> we buy at its top edge.
        # SHORT: price returns up into the OB -> we sell at its bottom edge.
        return self.top if self.direction is Dir.LONG else self.bottom


@dataclass
class Setup:
    """A fully-formed SLP trade setup, ready to place as a pending limit order."""
    direction: Dir
    entry: float
    stop: float
    target: float
    ob: OrderBlock
    bos_index: int
    signal_time: int

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        return self.reward / self.risk if self.risk else 0.0

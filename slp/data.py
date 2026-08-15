"""Data loading and helpers.

  * load_csv: read OHLC candles from a CSV (time,open,high,low,close[,volume]).
  * resample: aggregate a base timeframe up to a higher one (15m -> 1H).
  * synthetic: generate plausible random-walk candles for pipeline smoke tests.

Real historical data will come from the Deriv API (see broker module, later).
This module keeps the engine broker-agnostic and testable offline.
"""
from __future__ import annotations

import csv
import math
import random

from .model import Candle


def load_csv(path: str) -> list[Candle]:
    out: list[Candle] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(Candle(
                time=int(float(row["time"])),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            ))
    out.sort(key=lambda c: c.time)
    return out


def resample(candles: list[Candle], factor: int) -> list[Candle]:
    """Aggregate every `factor` candles into one (e.g. 4 x 15m -> 1H)."""
    out: list[Candle] = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i:i + factor]
        out.append(Candle(
            time=chunk[0].time,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
        ))
    return out


def synthetic(n: int = 4000, start: float = 2000.0, seed: int = 42,
              tf_seconds: int = 900, vol: float = 0.0015,
              trend_strength: float = 0.6) -> list[Candle]:
    """Random-walk candles with occasional trend regimes, for smoke-testing.

    Not a market model — just enough structure (impulses + pullbacks) to exercise
    the SLP engine end-to-end. Do NOT read anything into backtest numbers on this;
    real Deriv data replaces it before any conclusions.
    """
    rng = random.Random(seed)
    price = start
    t = 1_600_000_000
    out: list[Candle] = []
    drift = 0.0
    for i in range(n):
        if rng.random() < 0.02:  # occasionally flip/adjust the regime
            drift = rng.uniform(-1, 1) * vol * trend_strength
        o = price
        step = drift + rng.gauss(0, vol)
        c = o * (1 + step)
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol)) * 0.5)
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol)) * 0.5)
        out.append(Candle(time=t, open=round(o, 3), high=round(hi, 3),
                          low=round(lo, 3), close=round(c, 3)))
        price = c
        t += tf_seconds
    return out

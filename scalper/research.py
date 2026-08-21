"""Measure how the 12 M5 candles inside each 1H candle behave.

Run:  python -m scalper.research            # V75 + V100 + V25, ~live M5 history
      python -m scalper.research v75 12000

For each aligned hour (12 x M5, top-of-hour) we record:
  * WHERE the hour's high and the hour's low form (which M5 index 0..11).
  * Whether the FIRST M5's direction matches the hour's close direction.
  * OPENING-RANGE BREAKOUT: treat the first `OR_BARS` M5 candles as a range.
    Which side breaks first, and once broken does price reach +1R (continuation)
    before -1R (stop) — where R = the opening-range height. This is the core
    scalp edge candidate.
  * FOLLOW-THROUGH after the break bar closes beyond the range.

Everything here is pure measurement — no trading assumptions baked in beyond the
opening-range definition, which we sweep over a couple of sizes.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass

from slp.model import Candle

SYMBOLS = {"v75": "R_75", "v100": "R_100", "v25": "R_25",
           "v50": "R_50", "v10": "R_10",
           "btc": "cryBTCUSD", "eth": "cryETHUSD", "gold": "frxXAUUSD",
           "eurusd": "frxEURUSD", "gbpusd": "frxGBPUSD"}
M5 = 300
BARS_PER_HOUR = 12
OR_SIZES = (1, 2, 3)   # opening-range definitions to compare (in M5 bars)


@dataclass
class Hour:
    bars: list[Candle]   # exactly 12 M5 candles, aligned to the top of the hour

    @property
    def high(self) -> float:
        return max(b.high for b in self.bars)

    @property
    def low(self) -> float:
        return min(b.low for b in self.bars)

    @property
    def high_idx(self) -> int:
        return max(range(len(self.bars)), key=lambda i: self.bars[i].high)

    @property
    def low_idx(self) -> int:
        return min(range(len(self.bars)), key=lambda i: self.bars[i].low)

    @property
    def up(self) -> bool:
        return self.bars[-1].close >= self.bars[0].open


def group_hours(m5: list[Candle]) -> list[Hour]:
    """Bucket M5 candles into clean top-of-hour groups of exactly 12."""
    buckets: dict[int, list[Candle]] = {}
    for c in m5:
        key = c.time - (c.time % 3600)
        buckets.setdefault(key, []).append(c)
    hours = []
    for key in sorted(buckets):
        bars = sorted(buckets[key], key=lambda c: c.time)
        if len(bars) == BARS_PER_HOUR:   # only full, gap-free hours
            hours.append(Hour(bars))
    return hours


def _or_breakout_stats(hours: list[Hour], or_bars: int) -> dict:
    """For a given opening-range size, simulate the simplest scalp: after the OR
    forms, enter on the first break of OR-high (long) / OR-low (short) with stop
    at the opposite OR edge and target +1R. Measure hit-rate and expectancy."""
    wins = losses = neither = longs = shorts = 0
    for h in hours:
        or_bars_seq = h.bars[:or_bars]
        or_high = max(b.high for b in or_bars_seq)
        or_low = min(b.low for b in or_bars_seq)
        rng = or_high - or_low
        if rng <= 0:
            continue
        pos = None  # "L" / "S"
        entry = stop = target = 0.0
        resolved = False
        for b in h.bars[or_bars:]:
            if pos is None:
                # first touch of either edge arms the trade (breakout)
                if b.high >= or_high:
                    pos, entry, stop, target = "L", or_high, or_low, or_high + rng
                    longs += 1
                elif b.low <= or_low:
                    pos, entry, stop, target = "S", or_low, or_high, or_low - rng
                    shorts += 1
                # if it broke, check same bar for target/stop below
            if pos == "L":
                if b.low <= stop:
                    losses += 1; resolved = True; break
                if b.high >= target:
                    wins += 1; resolved = True; break
            elif pos == "S":
                if b.high >= stop:
                    losses += 1; resolved = True; break
                if b.low <= target:
                    wins += 1; resolved = True; break
        if pos is not None and not resolved:
            neither += 1
    total = wins + losses
    wr = wins / total if total else 0.0
    # +1R/-1R symmetric target -> expectancy per resolved trade in R:
    exp = wr * 1.0 + (1 - wr) * -1.0 if total else 0.0
    return {"or_bars": or_bars, "trades": total, "wins": wins, "losses": losses,
            "open_end": neither, "win_rate": wr, "exp_R": exp,
            "longs": longs, "shorts": shorts}


def analyze(symbol_key: str, count: int) -> None:
    from slp import deriv
    sym = SYMBOLS.get(symbol_key.lower(), symbol_key)
    m5 = deriv.fetch_candles_sync(sym, granularity=M5, count=count)
    hours = group_hours(m5)
    n = len(hours)
    print(f"\n===== {symbol_key.upper()} ({sym}) — {len(m5)} M5 bars -> {n} clean hours =====")
    if n < 50:
        print("  not enough clean hours; skipping."); return

    # 1) Where does the hour's extreme form?
    hi = Counter(h.high_idx for h in hours)
    lo = Counter(h.low_idx for h in hours)
    print("\n  WHERE the hour's HIGH forms (M5 index 0..11):")
    print("   ", " ".join(f"{i}:{100*hi[i]/n:4.1f}%" for i in range(BARS_PER_HOUR)))
    print("  WHERE the hour's LOW forms:")
    print("   ", " ".join(f"{i}:{100*lo[i]/n:4.1f}%" for i in range(BARS_PER_HOUR)))
    early_ext = sum(1 for h in hours if h.high_idx < 3 or h.low_idx < 3)
    print(f"  -> an extreme (high or low) lands in the first 3 bars in "
          f"{100*early_ext/n:.1f}% of hours.")

    # 2) Does the first M5 predict the hour's direction?
    agree = sum(1 for h in hours if h.bars[0].is_bull == h.up)
    print(f"\n  FIRST M5 direction == hour direction: {100*agree/n:.1f}% "
          f"(50% = no signal)")
    # first two M5 both same way -> stronger?
    twobull = [h for h in hours if h.bars[0].is_bull and h.bars[1].is_bull]
    twobear = [h for h in hours if h.bars[0].is_bear and h.bars[1].is_bear]
    if twobull:
        print(f"  first TWO M5 both bullish -> hour closes up "
              f"{100*sum(h.up for h in twobull)/len(twobull):.1f}% (n={len(twobull)})")
    if twobear:
        print(f"  first TWO M5 both bearish -> hour closes down "
              f"{100*sum(not h.up for h in twobear)/len(twobear):.1f}% (n={len(twobear)})")

    # 3) Opening-range breakout expectancy, swept over OR sizes
    print("\n  OPENING-RANGE BREAKOUT (enter on break, stop=opposite edge, target=+1R):")
    print(f"   {'OR':>3} {'trades':>7} {'win%':>6} {'exp(R)':>7} {'open-end':>9}  L/S")
    for k in OR_SIZES:
        s = _or_breakout_stats(hours, k)
        print(f"   {s['or_bars']:>3} {s['trades']:>7} {100*s['win_rate']:>5.1f} "
              f"{s['exp_R']:>7.3f} {s['open_end']:>9}  {s['longs']}/{s['shorts']}")


def main() -> None:
    args = sys.argv[1:]
    count = 12000
    keys = ["v75", "v100", "v25"]
    if args:
        if args[-1].isdigit():
            count = int(args[-1]); args = args[:-1]
        if args:
            keys = args
    for k in keys:
        try:
            analyze(k, count)
        except Exception as e:  # noqa: BLE001 — research script, keep going
            print(f"  {k}: fetch/analyze failed: {e}")


if __name__ == "__main__":
    main()

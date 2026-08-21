"""Strategy zoo — throw many distinct edges at the M5 data and see if ANY beats
a coin flip *before* costs. On a true random walk they should all sit at ~50%.

Run:  python -m scalper.zoo            # v75 v100 v25
      python -m scalper.zoo btc v75 20000

Each strategy is a generator of (entry_index, direction, stop_px, target_px).
We evaluate every trade with a common forward-walk (stop/target/time-stop),
report gross win% and avg R at ZERO cost. Anything not clearly > ~53% is dead.
"""
from __future__ import annotations

import sys
from statistics import fmean, pstdev

from slp.model import Candle

MAX_HOLD = 24


def _walk(m5, i, direction, stop, target):
    """Forward-walk one trade; return R (target reward assumed = |target-entry|/risk)."""
    entry = m5[i].open
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr = abs(target - entry) / risk
    end = min(len(m5), i + MAX_HOLD)
    for j in range(i, end):
        c = m5[j]
        if direction > 0:
            if c.low <= stop:
                return -1.0
            if c.high >= target:
                return rr
        else:
            if c.high >= stop:
                return -1.0
            if c.low <= target:
                return rr
    exit_px = m5[end - 1].close
    return ((exit_px - entry) if direction > 0 else (entry - exit_px)) / risk


def _eval(m5, signals):
    rs = []
    last_exit = -1
    for i, d, stop, target in signals:
        if i <= last_exit:      # non-overlapping
            continue
        r = _walk(m5, i, d, stop, target)
        if r is None:
            continue
        rs.append(r)
        last_exit = i  # approx; keeps it roughly non-overlapping
    if not rs:
        return (0, 0.0, 0.0)
    wins = sum(1 for r in rs if r > 0)
    return (len(rs), wins / len(rs), fmean(rs))


# ---- strategies: each yields (i, dir, stop_px, target_px) -------------------
def momentum2(m5):
    for i in range(2, len(m5)):
        a, b = m5[i - 2], m5[i - 1]
        if a.is_bull and b.is_bull:
            stop = min(a.low, b.low); yield (i, 1, stop, m5[i].open + 1.5 * (m5[i].open - stop))
        elif a.is_bear and b.is_bear:
            stop = max(a.high, b.high); yield (i, -1, stop, m5[i].open - 1.5 * (stop - m5[i].open))


def fade2(m5):
    for i in range(2, len(m5)):
        a, b = m5[i - 2], m5[i - 1]
        if a.is_bull and b.is_bull:   # fade the up-move
            stop = max(a.high, b.high); yield (i, -1, stop, m5[i].open - 1.5 * (stop - m5[i].open))
        elif a.is_bear and b.is_bear:
            stop = min(a.low, b.low); yield (i, 1, stop, m5[i].open + 1.5 * (m5[i].open - stop))


def zscore_revert(m5, win=20, z=2.0):
    for i in range(win, len(m5)):
        seg = m5[i - win:i]
        closes = [c.close for c in seg]
        mu = fmean(closes); sd = pstdev(closes)
        if sd <= 0:
            continue
        px = m5[i].open
        dev = (px - mu) / sd
        if dev >= z:      # stretched up -> fade short, target mean
            yield (i, -1, px + 1.5 * sd, mu)
        elif dev <= -z:   # stretched down -> long, target mean
            yield (i, 1, px - 1.5 * sd, mu)


def breakout20(m5, win=20):
    for i in range(win, len(m5)):
        seg = m5[i - win:i]
        hi = max(c.high for c in seg); lo = min(c.low for c in seg)
        px = m5[i].open; rng = hi - lo
        if rng <= 0:
            continue
        if m5[i - 1].close >= hi:      # closed above the range -> breakout long
            yield (i, 1, hi - 0.5 * rng, px + rng)
        elif m5[i - 1].close <= lo:
            yield (i, -1, lo + 0.5 * rng, px - rng)


def ma_cross(m5, fast=9, slow=21):
    if len(m5) <= slow:
        return
    closes = [c.close for c in m5]
    def sma(k, idx):
        return fmean(closes[idx - k:idx])
    for i in range(slow + 1, len(m5)):
        f0, s0 = sma(fast, i - 1), sma(slow, i - 1)
        f1, s1 = sma(fast, i), sma(slow, i)
        px = m5[i].open
        atr = fmean(c.high - c.low for c in m5[i - fast:i]) or 1e-9
        if f0 <= s0 and f1 > s1:      # golden cross
            yield (i, 1, px - 1.5 * atr, px + 2.25 * atr)
        elif f0 >= s0 and f1 < s1:
            yield (i, -1, px + 1.5 * atr, px - 2.25 * atr)


STRATS = {"momentum2": momentum2, "fade2": fade2, "zscore_revert": zscore_revert,
          "breakout20": breakout20, "ma_cross": ma_cross}


def main() -> None:
    from slp import deriv
    from scalper.research import SYMBOLS
    args = sys.argv[1:]
    count = 15000
    keys = ["v75", "v100", "v25"]
    if args:
        if args[-1].isdigit():
            count = int(args[-1]); args = args[:-1]
        if args:
            keys = args
    for k in keys:
        sym = SYMBOLS.get(k.lower(), k)
        try:
            m5 = deriv.fetch_candles_sync(sym, granularity=300, count=count)
        except Exception as e:  # noqa: BLE001
            print(f"{k}: fetch failed: {e}"); continue
        print(f"\n===== {k.upper()} ({sym}) — {len(m5)} M5 bars — GROSS (zero cost) =====")
        print(f"  {'strategy':<15} {'trades':>7} {'win%':>6} {'avgR':>7}")
        for name, fn in STRATS.items():
            n, wr, avg = _eval(m5, fn(m5))
            flag = "  <-- edge?" if (n > 100 and (wr > 0.53 or avg > 0.05)) else ""
            print(f"  {name:<15} {n:>7} {100*wr:>5.1f} {avg:>7.3f}{flag}")


if __name__ == "__main__":
    main()

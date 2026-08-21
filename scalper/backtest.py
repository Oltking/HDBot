"""Backtest the two-candle momentum scalp on M5 candles.

Edge (from scalper.research): when the first TWO M5 candles of an hour close the
same direction, the hour tends to continue that way ~70%. We scalp that
continuation intrabar:

  * Trigger: two consecutive same-direction M5 closes (the "engine").
  * Entry:   market at the open of the next M5.
  * Stop:    the opposite extreme of the two trigger candles (structure stop).
  * Target:  TP_R multiples of that risk.
  * Exit:    stop, target, or time-stop at the end of the hour (bar 11).

We DON'T restrict the trigger to the top of the hour — a rolling "two same-way
closes" fires all day, which is what makes it a high-frequency scalper. The
hourly study just told us the effect is real. Costs are modelled as a per-side
fraction of price (spread/commission), charged on entry and exit.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from slp.model import Candle

# realistic-ish round-trip cost for Deriv synthetic multipliers, as a fraction of
# price per side (tune to your account). 0.0002 = 2 bps/side = 4 bps round trip.
COST_PER_SIDE = 0.0002
TP_R = 1.5          # take-profit in R multiples
RISK_PER_TRADE = 0.01   # aggressive-but-bounded: 1% per scalp
MAX_HOLD = 12       # bars before time-stop (a scalp shouldn't linger)


@dataclass
class Result:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    total_r: float = 0.0
    balance: float = 0.0
    start_balance: float = 0.0
    peak: float = 0.0
    max_dd: float = 0.0
    rs: list = field(default_factory=list)

    def summary(self) -> str:
        wr = self.wins / self.trades if self.trades else 0.0
        ret = (self.balance / self.start_balance - 1) * 100 if self.start_balance else 0
        avg = self.total_r / self.trades if self.trades else 0
        return (f"Trades: {self.trades} | Win rate: {100*wr:.1f}% "
                f"({self.wins}W/{self.losses}L/{self.timeouts}TO)\n"
                f"Total R: {self.total_r:+.1f} | Avg R/trade: {avg:+.3f} | "
                f"Return: {ret:+.1f}% | Max DD: {self.max_dd:.1f}%\n"
                f"Balance: {self.start_balance:.2f} -> {self.balance:.2f}")


def run(m5: list[Candle], start_balance: float = 1000.0,
        tp_r: float = TP_R, cost: float = COST_PER_SIDE,
        risk: float = RISK_PER_TRADE) -> Result:
    r = Result(balance=start_balance, start_balance=start_balance, peak=start_balance)
    i = 2
    n = len(m5)
    while i < n:
        a, b = m5[i - 2], m5[i - 1]
        long_sig = a.is_bull and b.is_bull
        short_sig = a.is_bear and b.is_bear
        if not (long_sig or short_sig):
            i += 1
            continue

        entry = m5[i].open
        if long_sig:
            stop = min(a.low, b.low)
            risk_px = entry - stop
            if risk_px <= 0:
                i += 1; continue
            target = entry + tp_r * risk_px
        else:
            stop = max(a.high, b.high)
            risk_px = stop - entry
            if risk_px <= 0:
                i += 1; continue
            target = entry - tp_r * risk_px

        # walk forward through the hold window
        outcome_r = None
        j = i
        end = min(n, i + MAX_HOLD)
        while j < end:
            c = m5[j]
            if long_sig:
                if c.low <= stop:
                    outcome_r = -1.0; break
                if c.high >= target:
                    outcome_r = tp_r; break
            else:
                if c.high >= stop:
                    outcome_r = -1.0; break
                if c.low <= target:
                    outcome_r = tp_r; break
            j += 1
        timed_out = outcome_r is None
        if timed_out:
            # exit at the close of the last bar in the window, in R terms
            exit_px = m5[end - 1].close
            outcome_r = ((exit_px - entry) if long_sig else (entry - exit_px)) / risk_px

        # money: risk fraction of balance per 1R, minus round-trip cost in R
        cost_r = (2 * cost * entry) / risk_px   # two sides, as fraction of R
        net_r = outcome_r - cost_r
        r.balance += r.balance * risk * net_r
        r.total_r += net_r
        r.rs.append(net_r)
        r.trades += 1
        if timed_out:
            r.timeouts += 1
        if net_r > 0:
            r.wins += 1
        else:
            r.losses += 1
        r.peak = max(r.peak, r.balance)
        r.max_dd = max(r.max_dd, (r.peak - r.balance) / r.peak * 100)

        # jump past the trade so we don't re-enter mid-position (non-overlapping)
        i = max(j + 1, i + 1)
    return r


def main() -> None:
    from slp import deriv
    from scalper.research import SYMBOLS
    args = sys.argv[1:]
    count = 12000
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
        print(f"\n===== {k.upper()} ({sym}) — {len(m5)} M5 bars =====")
        for tp in (1.0, 1.5, 2.0):
            res = run(m5, tp_r=tp)
            print(f"  TP={tp}R  ", res.summary().replace("\n", "\n           "))


if __name__ == "__main__":
    main()

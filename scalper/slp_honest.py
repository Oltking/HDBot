"""Honest SLP backtest — removes the limit-fill look-ahead.

The original engine opens at the OB `entry` on the candle whose low/high *tapped*
it, then resolves that SAME candle — crediting a take-profit from an extreme that
may have printed BEFORE the fill. On provably random-walk synthetics this inflated
the win rate by ~12 points over the mathematical maximum.

Honest fill model:
  * A resting limit fills at `entry` when a candle taps it (same trigger as live).
  * On the FILL candle we ONLY allow the STOP to hit (conservative — if price ran
    entry->stop within that candle it's a real loss). The TARGET is NOT creditable
    on the fill candle, because that favourable extreme may predate the fill.
  * From the NEXT candle onward, resolve normally (SL first on a straddle).

Everything else — detector, bias, sizing, daily stop, costs — is unchanged.

Run:  python -m scalper.slp_honest                 # btc v75 v100 v25, all TFs
      python -m scalper.slp_honest btc 20000
"""
from __future__ import annotations

import sys

from slp import data, deriv
from slp.backtest import (BacktestResult, DAILY_STOP, RISK_PER_TRADE, Trade,
                          _bias_by_time, _day, _try_close)
from slp.model import Dir
from slp.strategy import SLPDetector

SYMBOLS = {"btc": "cryBTCUSD", "v75": "R_75", "v100": "R_100",
           "v25": "R_25", "v50": "R_50", "v10": "R_10"}

# (label, base_granularity, bias_factor)
CONFIGS = [
    ("M15 entry / H1 bias", 900, 4),
    ("M5 entry / M15 bias", 300, 3),
    ("M5 entry / H1 bias",  300, 12),
    ("M1 entry / M5 bias",  60,  5),
]


def _fill_candle_stopped(t: Trade, c) -> bool:
    """On the fill candle, only a stop-out is creditable (conservative)."""
    return c.low <= t.stop if t.direction is Dir.LONG else c.high >= t.stop


def run_honest(m15, h1=None, start_balance=1000.0, cost_price=0.0) -> BacktestResult:
    detector = SLPDetector()
    checkpoints = _bias_by_time(h1) if h1 else []
    result = BacktestResult(start_balance=start_balance, end_balance=start_balance)
    result.cost_price = cost_price
    balance = start_balance
    open_trade: Trade | None = None
    open_risk = 0.0
    day_start = balance
    day = None
    cp_i = 0

    for c in m15:
        d = _day(c.time)
        if d != day:
            day = d; day_start = balance

        # 1) resolve an OPEN trade on this (post-fill) candle
        if open_trade is not None:
            if _try_close(open_trade, c, cost_price):
                t = open_trade
                t.pnl = open_risk * t.result_r
                balance += t.pnl
                result.trades.append(t)
                open_trade = None

        # 2) bias + detector
        if checkpoints:
            while cp_i + 1 < len(checkpoints) and checkpoints[cp_i + 1][0] <= c.time:
                cp_i += 1
            detector.set_bias(checkpoints[cp_i][1] if checkpoints[cp_i][0] <= c.time else None)
        setup = detector.update(c)

        daily_dd = (day_start - balance) / day_start if day_start else 0
        if setup is not None and open_trade is None and daily_dd < DAILY_STOP:
            open_trade = Trade(direction=setup.direction, entry=setup.entry,
                               stop=setup.stop, target=setup.target, open_time=c.time)
            open_risk = balance * RISK_PER_TRADE
            # HONEST: on the fill candle, only a stop can close it (no TP credit).
            if _fill_candle_stopped(open_trade, c):
                risk = abs(open_trade.entry - open_trade.stop)
                cost_r = cost_price / risk if risk else 0.0
                open_trade.exit, open_trade.won = open_trade.stop, False
                open_trade.result_r = -1.0 - cost_r
                open_trade.close_time = c.time
                open_trade.pnl = open_risk * open_trade.result_r
                balance += open_trade.pnl
                result.trades.append(open_trade)
                open_trade = None

    result.end_balance = balance
    return result


def main() -> None:
    args = sys.argv[1:]
    count = 15000
    keys = ["btc", "v75", "v100", "v25"]
    cost = 0.0
    if args:
        if args[-1].isdigit():
            count = int(args[-1]); args = args[:-1]
        if args:
            keys = args
    for k in keys:
        sym = SYMBOLS.get(k.lower(), k)
        print(f"\n===== {k.upper()} ({sym}) — HONEST fill model =====")
        cache: dict[int, list] = {}
        for label, gran, factor in CONFIGS:
            try:
                if gran not in cache:
                    cache[gran] = deriv.fetch_candles_sync(sym, granularity=gran, count=count)
                base = cache[gran]
                bias = data.resample(base, factor=factor)
                res = run_honest(base, h1=bias, cost_price=cost)
                print(f"  {label:<22} " + res.summary().replace("\n", "\n  " + " " * 22))
            except Exception as e:  # noqa: BLE001
                print(f"  {label:<22} failed: {e}")


if __name__ == "__main__":
    main()

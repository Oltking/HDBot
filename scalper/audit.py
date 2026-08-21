"""Audit the SLP backtester for intrabar look-ahead.

Hypothesis: slp.backtest resolves a trade on its own entry candle (backtest.py
:166), crediting a TP/SL from extremes that may have printed before the fill.
That manufactures fake wins — especially on lower timeframes and on random-walk
synthetics where no real edge can exist.

This re-runs the SAME detector/signals but resolves each trade ONLY on candles
strictly AFTER the fill candle (causal). If the edge collapses toward 50% on the
synthetics, the original numbers were an artifact.
"""
from __future__ import annotations

import sys

from slp import backtest, data, deriv
from slp.backtest import DAILY_STOP, RISK_PER_TRADE, Trade, _try_close, _day, BacktestResult
from slp.strategy import SLPDetector
from slp.structure import StructureTracker
from slp.model import Dir

SYMBOLS = {"btc": "cryBTCUSD", "v75": "R_75", "v100": "R_100", "v25": "R_25"}


def run_causal(m15, h1=None, start_balance=1000.0, same_candle=False):
    detector = SLPDetector()
    checkpoints = backtest._bias_by_time(h1) if h1 else []
    result = BacktestResult(start_balance=start_balance, end_balance=start_balance)
    balance = start_balance
    open_trade = None
    open_risk = 0.0
    day_start = balance
    day = None
    cp_i = 0
    for c in m15:
        d = _day(c.time)
        if d != day:
            day = d; day_start = balance
        # manage existing (candles strictly after fill only)
        if open_trade is not None:
            if _try_close(open_trade, c):
                t = open_trade
                t.pnl = open_risk * t.result_r
                balance += t.pnl
                result.trades.append(t)
                open_trade = None
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
            if same_candle and _try_close(open_trade, c):  # the original behaviour
                t = open_trade
                t.pnl = open_risk * t.result_r
                balance += t.pnl
                result.trades.append(t)
                open_trade = None
    result.end_balance = balance
    return result


def main():
    args = sys.argv[1:]
    count = 15000
    keys = ["btc", "v75", "v100", "v25"]
    if args:
        if args[-1].isdigit():
            count = int(args[-1]); args = args[:-1]
        if args:
            keys = args
    for k in keys:
        sym = SYMBOLS.get(k.lower(), k)
        m15 = deriv.fetch_candles_sync(sym, granularity=900, count=count)
        h1 = data.resample(m15, factor=4)
        orig = run_causal(m15, h1, same_candle=True)    # == slp.backtest
        caus = run_causal(m15, h1, same_candle=False)   # strictly causal
        print(f"\n{k.upper()} ({sym})  M15/H1, {len(m15)} candles")
        print(f"  original (same-candle resolve): {orig.win_rate*100:5.1f}% win, "
              f"{orig.n:4d} trades, {orig.total_r:+7.1f}R, ret {orig.return_pct:+.0f}%")
        print(f"  causal   (next-candle resolve): {caus.win_rate*100:5.1f}% win, "
              f"{caus.n:4d} trades, {caus.total_r:+7.1f}R, ret {caus.return_pct:+.0f}%")


if __name__ == "__main__":
    main()

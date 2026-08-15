#!/usr/bin/env python3
"""Run an SLP backtest.

Usage:
  python run_backtest.py                 # synthetic smoke-test data
  python run_backtest.py data/xau_15m.csv  # your own 15m CSV (time,open,high,low,close)

The CSV path is treated as 15m candles; the 1H bias is derived by resampling.
"""
import sys

from slp import backtest, data

# Friendly names -> Deriv symbol codes.
SYMBOLS = {
    "gold": "frxXAUUSD", "xauusd": "frxXAUUSD",
    "btc": "cryBTCUSD", "eth": "cryETHUSD",
    "eurusd": "frxEURUSD", "gbpusd": "frxGBPUSD",
    "v75": "R_75", "v100": "R_100",
}


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    if arg is None:
        m15 = data.synthetic(n=6000)
        source = "synthetic (smoke-test only — not real market data)"
    elif arg.endswith(".csv"):
        m15 = data.load_csv(arg)
        source = arg
    else:
        from slp import deriv
        sym = SYMBOLS.get(arg.lower(), arg)
        m15 = deriv.fetch_candles_sync(sym, granularity=900, count=count)
        source = f"Deriv live history: {sym} ({arg})"

    h1 = data.resample(m15, factor=4)
    result = backtest.run(m15, h1=h1, start_balance=1000.0)

    print(f"Source: {source}")
    print(f"15m candles: {len(m15)} | 1H candles: {len(h1)}")
    print("-" * 48)
    print(result.summary())


if __name__ == "__main__":
    main()

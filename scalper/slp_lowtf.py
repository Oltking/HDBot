"""Run the *existing* SLP engine on lower timeframes.

The live bot trades M15 entries with an H1 bias (bias = 4x the entry TF). This
asks: does the same Structure->Liquidity->POI logic still have an edge when we
speed it up? We fetch one base timeframe and resample the bias TF from it, then
run the untouched slp.backtest.run.

Configs tested (entry / bias):
    M5  / M15   (bias factor 3)   -> faster, more trades
    M5  / H1    (bias factor 12)  -> M5 entries but slow, strong bias
    M1  / M5    (bias factor 5)   -> very fast

Run:  python -m scalper.slp_lowtf              # btc v75 v100 v25
      python -m scalper.slp_lowtf v75 20000
"""
from __future__ import annotations

import sys

from slp import backtest, data, deriv

SYMBOLS = {"btc": "cryBTCUSD", "v75": "R_75", "v100": "R_100",
           "v25": "R_25", "v50": "R_50", "v10": "R_10"}

# (label, base_granularity_seconds, bias_resample_factor)
CONFIGS = [
    ("M5 entry / M15 bias", 300, 3),
    ("M5 entry / H1 bias",  300, 12),
    ("M1 entry / M5 bias",  60,  5),
]


def run_symbol(key: str, count: int) -> None:
    sym = SYMBOLS.get(key.lower(), key)
    print(f"\n===== {key.upper()} ({sym}) =====")
    # fetch once per base granularity we need
    cache: dict[int, list] = {}
    for label, gran, factor in CONFIGS:
        try:
            if gran not in cache:
                cache[gran] = deriv.fetch_candles_sync(sym, granularity=gran, count=count)
            base = cache[gran]
            bias = data.resample(base, factor=factor)
            res = backtest.run(base, h1=bias, start_balance=1000.0)
            print(f"  {label:<22} "
                  + res.summary().replace("\n", "\n  " + " " * 22))
        except Exception as e:  # noqa: BLE001
            print(f"  {label:<22} failed: {e}")


def main() -> None:
    args = sys.argv[1:]
    count = 15000
    keys = ["btc", "v75", "v100", "v25"]
    if args:
        if args[-1].isdigit():
            count = int(args[-1]); args = args[:-1]
        if args:
            keys = args
    print(f"SLP on lower timeframes — {count} base candles/symbol")
    for k in keys:
        run_symbol(k, count)


if __name__ == "__main__":
    main()

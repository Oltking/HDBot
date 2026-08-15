"""Compare LIVE demo results (logs/trades.csv) against the validated backtest.

Pairs each open/close event by contract_id, derives the realized R multiple
(pnl / the 2%-risk amount at entry), and prints per-symbol + overall stats next
to the backtest baseline so the go/no-go call is obvious.

Run:  python -m slp.compare            (reads $SLP_LOG_DIR/trades.csv)
      python -m slp.compare path.csv   (explicit file)
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict

from .backtest import RISK_PER_TRADE
from .persistence import TRADES_CSV

# Backtest baseline (recent-window, with costs, 50% floor) — the numbers to beat.
BACKTEST_BASELINE = {
    "cryBTCUSD": {"win": 0.57, "trades": 277, "note": "BTC"},
    "R_75":      {"win": 0.55, "trades": 249, "note": "V75"},
}


def _load(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _pair_trades(rows: list[dict]) -> list[dict]:
    """Match open+close events by contract_id into completed trades."""
    opens: dict[str, dict] = {}
    trades: list[dict] = []
    for r in rows:
        cid = r.get("contract_id")
        if not cid:
            continue
        if r.get("event") == "open":
            opens[cid] = r
        elif r.get("event") == "close":
            o = opens.pop(cid, None)
            pnl = float(r.get("pnl") or 0.0)
            open_bal = float(o.get("balance")) if o and o.get("balance") else None
            risk = open_bal * RISK_PER_TRADE if open_bal else None
            trades.append({
                "symbol": r.get("symbol") or (o or {}).get("symbol") or "?",
                "pnl": pnl,
                "r": (pnl / risk) if risk else None,
                "won": pnl > 0,
                "close_balance": float(r.get("balance") or 0.0),
            })
    return trades, len(opens)  # open positions still running


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for t in trades if t["won"])
    rs = [t["r"] for t in trades if t["r"] is not None]
    balances = [t["close_balance"] for t in trades if t["close_balance"]]
    max_dd = 0.0
    if balances:
        peak = balances[0]
        for b in balances:
            peak = max(peak, b)
            max_dd = max(max_dd, (peak - b) / peak if peak else 0)
    return {
        "n": n, "wins": wins, "win": wins / n,
        "total_r": sum(rs) if rs else 0.0,
        "pnl": sum(t["pnl"] for t in trades),
        "max_dd": max_dd * 100,
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else TRADES_CSV
    try:
        rows = _load(path)
    except FileNotFoundError:
        print(f"No trade log yet at {path} — the bot hasn't closed any trades.")
        return

    trades, still_open = _pair_trades(rows)
    if not trades:
        print(f"{path}: no completed trades yet"
              f"{f' ({still_open} still open)' if still_open else ''}.")
        return

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)

    print(f"LIVE results from {path}"
          f"{f'  ({still_open} position(s) still open)' if still_open else ''}")
    print("=" * 68)
    hdr = f"{'symbol':10}{'trades':>7}{'live win%':>10}{'bt win%':>9}{'totR':>7}{'P&L$':>9}{'DD%':>6}"
    print(hdr); print("-" * 68)
    for sym in sorted(by_symbol):
        s = _stats(by_symbol[sym])
        bt = BACKTEST_BASELINE.get(sym, {})
        bt_win = f"{bt['win']*100:.0f}" if bt else "—"
        print(f"{bt.get('note', sym):10}{s['n']:7}{s['win']*100:9.1f}%{bt_win:>8}%"
              f"{s['total_r']:+7.1f}{s['pnl']:+9.2f}{s['max_dd']:6.1f}")
    print("-" * 68)
    overall = _stats(trades)
    print(f"{'OVERALL':10}{overall['n']:7}{overall['win']*100:9.1f}%{'':>9}"
          f"{overall['total_r']:+7.1f}{overall['pnl']:+9.2f}{overall['max_dd']:6.1f}")
    print("=" * 68)

    # Simple verdict cue.
    n = overall["n"]
    if n < 20:
        print(f"\nOnly {n} trades — too few to judge. Aim for 30+ before deciding.")
    else:
        gap = overall["win"] - 0.55
        if gap >= -0.05:
            print("\nLive win-rate is tracking the backtest. Promising — keep going.")
        else:
            print("\nLive win-rate is meaningfully below backtest. Investigate before real money "
                  "(likely the entry-fidelity gap: live fills at candle-close spot, not the OB price).")


if __name__ == "__main__":
    main()

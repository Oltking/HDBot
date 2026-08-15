"""Trade journaling + crash-safe position state for the live bot.

  * trades.csv  — one row per closed (and opened) trade, for comparing live
    results against the backtest. Appended, never rewritten.
  * position.json — the single currently-open position, so a restart can
    reconcile it with Deriv instead of forgetting it and double-opening.

Paths default to ./logs and ./state (created on demand).
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

LOG_DIR = os.environ.get("SLP_LOG_DIR", "logs")
STATE_DIR = os.environ.get("SLP_STATE_DIR", "state")
TRADES_CSV = os.path.join(LOG_DIR, "trades.csv")
POSITION_JSON = os.path.join(STATE_DIR, "position.json")

_TRADE_FIELDS = [
    "event", "time_utc", "symbol", "direction", "contract_id",
    "entry", "stop", "target", "rr", "stake", "multiplier",
    "pnl", "balance",
]


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_trade(**row) -> None:
    """Append one trade event ('open' or 'close') to trades.csv."""
    os.makedirs(LOG_DIR, exist_ok=True)
    row.setdefault("time_utc", _utc_now())
    new_file = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_TRADE_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


# ---- open-position state (crash recovery) -------------------------------

def save_position(pos) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(POSITION_JSON, "w") as f:
        json.dump(asdict(pos) if hasattr(pos, "__dataclass_fields__") else pos, f)


def load_position() -> dict | None:
    if not os.path.exists(POSITION_JSON):
        return None
    try:
        with open(POSITION_JSON) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def clear_position() -> None:
    try:
        os.remove(POSITION_JSON)
    except FileNotFoundError:
        pass

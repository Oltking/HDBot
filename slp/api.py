"""Tiny read-only HTTP API exposing the live bot's state for the dashboard.

Runs in a daemon thread alongside the asyncio trading loop and reads a shared
LiveTrader instance (plain attribute reads — a slightly stale snapshot is fine
for a dashboard). Serves:

  GET /              -> "ok" (Render health check)
  GET /api/status    -> balance, per-symbol bias, open position, last candle, ...
  GET /api/trades    -> paired trades from trades.csv + summary stats

CORS is wide-open (read-only public data) so the Vercel frontend can fetch it.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import compare, persistence
from .backtest import DAILY_STOP, RISK_PER_TRADE


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def status_payload(trader, started_at: int) -> dict:
    symbols = [
        {"symbol": s,
         "bias": (st.bias().name if st.bias() else None),
         "warmed": st.warmed,
         "ready": st.ready}
        for s, st in trader.states.items()
    ]
    pos = None
    if trader.position is not None:
        p = trader.position
        pos = {"symbol": p.symbol, "direction": p.direction, "entry": p.entry,
               "stop": p.stop, "target": p.target, "rr": p.rr, "stake": p.stake,
               "multiplier": p.multiplier, "contract_id": p.contract_id}
    last = max((st.last_open_time or 0) for st in trader.states.values())
    return {
        "alive": True,
        "mode": "live-demo" if trader.place_orders else "paper",
        "balance": round(trader.balance, 2),
        "day_start_balance": round(trader.day_start_balance, 2),
        "daily_pnl": round(trader.balance - trader.day_start_balance, 2),
        "risk_per_trade": RISK_PER_TRADE,
        "daily_stop": DAILY_STOP,
        "timeframe": f"{trader.granularity//60}m/{trader.granularity*trader.bias_factor//60}m",
        "sizing": ("min-stake" if getattr(trader, "min_stake_mode", False) else "risk-2pct"),
        "symbols": symbols,
        "position": pos,
        "last_candle": last,
        "started_at": started_at,
        "uptime_seconds": _now() - started_at,
        "server_time": _now(),
    }


def trades_payload() -> dict:
    try:
        rows = compare._load(persistence.TRADES_CSV)
    except FileNotFoundError:
        return {"trades": [], "summary": {"n": 0}, "open": 0}
    trades, still_open = compare._pair_trades(rows)
    stats = compare._stats(trades)
    # newest first, cap for payload size
    return {"trades": list(reversed(trades))[:100], "summary": stats, "open": still_open}


def _make_handler(trader, started_at: int):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict | str):
            payload = body if isinstance(body, str) else json.dumps(body)
            data = payload.encode()
            self.send_response(code)
            self.send_header("Content-Type",
                             "text/plain" if isinstance(body, str) else "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if self.path.startswith("/api/status"):
                    self._send(200, status_payload(trader, started_at))
                elif self.path.startswith("/api/trades"):
                    self._send(200, trades_payload())
                else:
                    self._send(200, "ok")
            except Exception as e:  # never let the dashboard take the bot down
                self._send(500, {"error": str(e)})

        def log_message(self, *args):  # silence per-request logging
            pass

    return Handler


def start_api_server(trader, started_at: int, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(trader, started_at))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"API server listening on :{port} (/api/status, /api/trades)")
    return server

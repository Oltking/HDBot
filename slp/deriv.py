"""Deriv WebSocket client — historical candles (and later, live trading).

Docs: https://api.deriv.com/  — endpoint wss://ws.derivws.com/websockets/v3

Symbol names (Deriv's own codes):
  gold   -> frxXAUUSD      EURUSD -> frxEURUSD     GBPUSD -> frxGBPUSD
  BTC    -> cryBTCUSD      ETH    -> cryETHUSD
  Vol 75 -> R_75           Vol 100 -> R_100        (synthetic indices)

Granularity is in seconds; 15m = 900. History fetch needs no token; trading does.
"""
from __future__ import annotations

import asyncio
import json

import websockets

from . import config
from .model import Candle

# Market data uses the legacy UNAUTHENTICATED endpoint, which needs a numeric
# app_id (1089 is Deriv's public default). This is independent of DERIV_APP_ID,
# which is now the NEW-API app_id used only for authenticated trading (deriv_v2).
_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
_MAX_PER_REQ = 5000  # Deriv caps candles per ticks_history call


_THROTTLE = 0.6   # seconds between paginated requests (respect Deriv rate limits)
_RETRIES = 4      # retries on transient/rate-limit errors


async def _fetch_chunk(ws, symbol: str, granularity: int, count: int,
                       end: int | str, _attempt: int = 0) -> list[Candle]:
    await ws.send(json.dumps({
        "ticks_history": symbol,
        "style": "candles",
        "granularity": granularity,
        "count": count,
        "end": end,
    }))
    while True:
        msg = json.loads(await ws.recv())
        if "error" in msg:
            code = msg["error"].get("code", "")
            # Deriv throws a generic error under load; back off and retry.
            if _attempt < _RETRIES:
                await asyncio.sleep(1.5 * (_attempt + 1))
                return await _fetch_chunk(ws, symbol, granularity, count, end, _attempt + 1)
            raise RuntimeError(f"Deriv error [{code}]: {msg['error'].get('message')}")
        if msg.get("msg_type") == "candles":
            return [
                Candle(time=int(c["epoch"]), open=float(c["open"]),
                       high=float(c["high"]), low=float(c["low"]),
                       close=float(c["close"]))
                for c in msg["candles"]
            ]


async def fetch_candles(symbol: str, granularity: int = 900,
                        count: int = 5000) -> list[Candle]:
    """Fetch the `count` most-recent candles, paginating backwards as needed.

    Each page requests a full _MAX_PER_REQ window (small requests can land inside
    a weekend/holiday gap and return empty). We stop when we have enough, or when
    a page returns fewer than a page-worth of *new* bars (no more history).
    """
    dedup: dict[int, Candle] = {}
    end: int | str = "latest"
    async with websockets.connect(_WS_URL) as ws:
        while len(dedup) < count:
            chunk = await _fetch_chunk(ws, symbol, granularity, _MAX_PER_REQ, end)
            if not chunk:
                break
            new = 0
            for c in chunk:
                if c.time not in dedup:
                    dedup[c.time] = c
                    new += 1
            if new == 0:
                break  # nothing older left to fetch
            end = min(dedup) - 1  # next page ends just before our oldest bar
            await asyncio.sleep(_THROTTLE)  # stay under Deriv's rate limit
    ordered = [dedup[t] for t in sorted(dedup)]
    return ordered[-count:] if len(ordered) > count else ordered


def fetch_candles_sync(symbol: str, granularity: int = 900,
                       count: int = 5000) -> list[Candle]:
    return asyncio.run(fetch_candles(symbol, granularity, count))

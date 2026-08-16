"""Live SLP loop on Deriv.

Two connections, two purposes:
  * DATA — legacy unauthenticated endpoint (app_id 1089), polled every POLL_SECONDS
    for newly-closed 15m candles. Feeds each CLOSED candle to a per-symbol
    SLPDetector, exactly as the backtest does (live == tested logic).
  * TRADING — the NEW Deriv API via DerivV2 (PAT + registered app_id -> OTP -> WS),
    used to place/close multiplier orders on the demo account.

Default is PAPER mode (no orders). Pass place_orders=True (CLI --live-demo) to send
real DEMO orders; it refuses any non-demo account.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import websockets

from . import deriv, persistence
from .backtest import DAILY_STOP, RISK_PER_TRADE
from .deriv_v2 import DerivV2, DerivV2Error
from .model import Candle, Dir
from .strategy import SLPDetector
from .structure import StructureTracker

DATA_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"  # legacy, data only
GRANULARITY = 900          # 15m
H1_FACTOR = 4              # 4 x 15m = 1H
WARMUP = 400               # seed candles before we trust signals
POLL_SECONDS = 30          # how often to poll for a newly-closed candle
HEARTBEAT_SECONDS = 1800   # log an "alive" line at least this often (30 min)


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


@dataclass
class SymbolState:
    symbol: str
    detector: SLPDetector = field(default_factory=SLPDetector)
    h1_tracker: StructureTracker = field(default_factory=StructureTracker)
    h1_bucket: list[Candle] = field(default_factory=list)
    last_open_time: int | None = None
    warmed: int = 0
    ready: bool = False   # True once initial history is seeded -> allowed to trade

    def on_closed_candle(self, c: Candle) -> None:
        self.h1_bucket.append(c)
        if len(self.h1_bucket) == H1_FACTOR:
            b = self.h1_bucket
            h1 = Candle(time=b[0].time, open=b[0].open,
                        high=max(x.high for x in b), low=min(x.low for x in b),
                        close=b[-1].close)
            self.h1_tracker.update(h1)
            self.h1_bucket = []
        self.detector.set_bias(self.h1_tracker.trend)

    def bias(self) -> Dir | None:
        return self.h1_tracker.trend


@dataclass
class OpenPosition:
    symbol: str
    contract_id: str
    stake: float
    direction: str = ""       # "LONG"/"SHORT"
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    rr: float = 0.0
    multiplier: int = 0


class LiveTrader:
    def __init__(self, symbols: list[str], balance: float = 1000.0,
                 place_orders: bool = False):
        self.symbols = symbols
        self.states = {s: SymbolState(s) for s in symbols}
        self.balance = balance
        self.place_orders = place_orders
        self.day_start_balance = balance
        self.current_day: str | None = None
        self.position: OpenPosition | None = None  # one at a time (matches backtest)
        self.broker: DerivV2 | None = None
        self._last_heartbeat = 0

    async def run(self) -> None:
        mode = "LIVE-DEMO ORDERS" if self.place_orders else "PAPER (no orders)"
        print(f"[{_fmt(_now())}] SLP live starting — {mode}")
        print(f"Symbols: {', '.join(self.symbols)} | risk {RISK_PER_TRADE*100:.0f}% | "
              f"daily stop {DAILY_STOP*100:.0f}%")

        if self.place_orders:
            self.broker = DerivV2()
            acct = await self.broker.connect(demo_only=True)  # raises if no demo
            self.balance = float(acct["balance"])
            self.day_start_balance = self.balance
            print(f"Trading account: {acct['account_id']} ({acct['account_type']}) "
                  f"balance=${self.balance:.2f}")
            await self._recover_position()

        # Data loop with auto-reconnect: the legacy socket will drop over days;
        # detector state lives in memory, so we just reopen and keep polling.
        first = True
        while True:
            try:
                async with websockets.connect(DATA_WS_URL) as ws:
                    if first:
                        for s in self.symbols:
                            candles = await self._fetch(ws, s, WARMUP)
                            st = self.states[s]
                            for candle in candles[:-1]:
                                st.on_closed_candle(candle)
                                st.detector.update(candle)
                                st.warmed += 1
                            st.last_open_time = candles[-2].time if len(candles) >= 2 else None
                            st.ready = True   # history seeded -> this symbol may trade
                            print(f"Seeded {s}: {st.warmed} candles, bias={st.bias()}")
                        first = False
                        print(f"Polling every {POLL_SECONDS}s for newly-closed 15m candles. "
                              f"Ctrl-C to stop.")
                    else:
                        print(f"[{_fmt(_now())}] data socket reconnected.")
                    while True:
                        await asyncio.sleep(POLL_SECONDS)
                        await self._manage_position()
                        if self.broker is not None and self.position is None:
                            try:
                                await self.broker.ping()  # keep trading socket warm
                            except DerivV2Error:
                                pass
                        for s in self.symbols:
                            candles = await self._fetch(ws, s, 5)
                            await self._ingest_poll(s, candles)
                        self._maybe_heartbeat()
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                print(f"[{_fmt(_now())}] data socket dropped ({e}); reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _fetch(self, ws, symbol: str, count: int) -> list[Candle]:
        await ws.send(json.dumps({
            "ticks_history": symbol, "style": "candles", "granularity": GRANULARITY,
            "count": count, "end": "latest",
        }))
        while True:
            m = json.loads(await ws.recv())
            if "error" in m:
                raise RuntimeError(m["error"].get("message"))
            if m.get("msg_type") == "candles":
                return [Candle(time=int(c["epoch"]), open=float(c["open"]),
                               high=float(c["high"]), low=float(c["low"]),
                               close=float(c["close"])) for c in m["candles"]]

    async def _ingest_poll(self, symbol: str, candles: list[Candle]) -> None:
        st = self.states[symbol]
        if len(candles) < 2:
            return
        for c in candles[:-1]:  # exclude the still-forming last candle
            if st.last_open_time is None or c.time > st.last_open_time:
                await self._process_closed(symbol, c)
                st.last_open_time = c.time

    async def _process_closed(self, symbol: str, candle: Candle) -> None:
        st = self.states[symbol]
        st.on_closed_candle(candle)
        setup = st.detector.update(candle)

        day = _fmt(candle.time)[:10]
        if day != self.current_day:
            self.current_day = day
            self.day_start_balance = self.balance

        if setup is None or not st.ready:
            return
        if self.position is not None:
            return  # one position at a time
        daily_dd = (self.day_start_balance - self.balance) / self.day_start_balance
        if daily_dd >= DAILY_STOP:
            print(f"[{_fmt(candle.time)}] {symbol}: daily stop hit ({daily_dd*100:.1f}%), skipping.")
            return

        risk_amount = self.balance * RISK_PER_TRADE
        d = "LONG" if setup.direction is Dir.LONG else "SHORT"
        print(f"[{_fmt(candle.time)}] SIGNAL {symbol} {d} "
              f"entry={setup.entry:.4f} SL={setup.stop:.4f} TP={setup.target:.4f} "
              f"RR={setup.rr:.2f} risk=${risk_amount:.2f}")
        if self.place_orders:
            await self._place(symbol, setup, risk_amount)
        else:
            print("   (paper mode — no order sent)")

    async def _place(self, symbol: str, setup, risk_amount: float) -> None:
        """Open a multiplier position sized so its SL amount == our 2% risk.

        stake = risk_amount / (multiplier * f_sl),  f_sl = |entry-stop|/entry
        limit_order.stop_loss  = risk_amount        (money)
        limit_order.take_profit = risk_amount * RR   (money)
        """
        assert self.broker is not None
        up = setup.direction is Dir.LONG
        f_sl = abs(setup.entry - setup.stop) / setup.entry
        try:
            mults = await self.broker.multipliers_for(symbol, up)
            if not mults:
                print("   no multipliers available — skipping"); return
            multiplier = min(mults)
            stake = risk_amount / (multiplier * f_sl)
            stake = max(1.0, min(stake, self.balance))  # clamp to [min, balance]
            prop = await self.broker.proposal(
                symbol, up, stake, multiplier,
                stop_loss=risk_amount, take_profit=risk_amount * setup.rr)
            b = await self.broker.buy(prop["id"], max_price=prop["ask_price"])
            self.position = OpenPosition(
                symbol=symbol, contract_id=str(b["contract_id"]), stake=stake,
                direction="LONG" if up else "SHORT", entry=setup.entry,
                stop=setup.stop, target=setup.target, rr=setup.rr, multiplier=multiplier)
            persistence.save_position(self.position)
            persistence.log_trade(
                event="open", symbol=symbol, direction=self.position.direction,
                contract_id=self.position.contract_id, entry=setup.entry,
                stop=setup.stop, target=setup.target, rr=round(setup.rr, 3),
                stake=round(stake, 2), multiplier=multiplier, balance=round(self.balance, 2))
            print(f"   ORDER FILLED: contract={b['contract_id']} stake=${stake:.2f} "
                  f"mult={multiplier} buy=${b.get('buy_price')}")
        except DerivV2Error as e:
            print(f"   ORDER REJECTED: {e}")

    def _maybe_heartbeat(self) -> None:
        """Periodically log that the bot is alive + current bias, so a quiet
        stretch is visibly a quiet stretch (not a silent crash)."""
        now = _now()
        if now - self._last_heartbeat < HEARTBEAT_SECONDS:
            return
        self._last_heartbeat = now
        biases = " ".join(
            f"{s}={(st.bias().name if st.bias() else 'none')}"
            for s, st in self.states.items())
        last = max((st.last_open_time or 0) for st in self.states.values())
        pos = self.position.symbol if self.position else "flat"
        print(f"[{_fmt(now)}] alive · {biases} · pos={pos} · "
              f"bal=${self.balance:.2f} · last_candle={_fmt(last) if last else '—'}")

    async def _recover_position(self) -> None:
        """On startup, restore a position left open by a previous run so we don't
        forget it (and double-open). Reconcile its live status with Deriv."""
        saved = persistence.load_position()
        if not saved or self.broker is None:
            return
        cid = saved.get("contract_id")
        print(f"Recovering saved position: contract={cid} ({saved.get('symbol')})")
        try:
            oc = await self.broker.open_contract(cid)
        except DerivV2Error as e:
            print(f"   could not query contract {cid} ({e}); clearing stale state.")
            persistence.clear_position()
            return
        if oc.get("is_sold"):
            profit = float(oc.get("profit", 0.0))
            self.balance += profit
            persistence.log_trade(
                event="close", symbol=saved.get("symbol"),
                direction=saved.get("direction", ""), contract_id=cid,
                pnl=round(profit, 2), balance=round(self.balance, 2))
            persistence.clear_position()
            print(f"   it already closed while we were down: P&L=${profit:+.2f}")
        else:
            self.position = OpenPosition(**saved)
            print(f"   still open — resuming management of contract {cid}.")

    async def _manage_position(self) -> None:
        """Poll the open contract; when Deriv has closed it (SL/TP hit), realize
        the P&L into balance and free the slot."""
        if self.position is None or self.broker is None:
            return
        try:
            oc = await self.broker.open_contract(self.position.contract_id)
        except DerivV2Error as e:
            print(f"   position poll error: {e}"); return
        if oc.get("is_sold"):
            profit = float(oc.get("profit", 0.0))
            self.balance += profit
            pos = self.position
            persistence.log_trade(
                event="close", symbol=pos.symbol, direction=pos.direction,
                contract_id=pos.contract_id, entry=pos.entry, stop=pos.stop,
                target=pos.target, rr=round(pos.rr, 3), stake=round(pos.stake, 2),
                multiplier=pos.multiplier, pnl=round(profit, 2),
                balance=round(self.balance, 2))
            persistence.clear_position()
            print(f"[{_fmt(_now())}] CLOSED {pos.symbol} contract={pos.contract_id} "
                  f"P&L=${profit:+.2f} balance=${self.balance:.2f}")
            self.position = None


def main(symbols: list[str] | None = None, place_orders: bool = False) -> None:
    import os
    syms = symbols or ["cryBTCUSD", "R_75"]  # BTC + V75
    trader = LiveTrader(syms, place_orders=place_orders)
    # If a PORT is set (Render web service), expose the read-only dashboard API.
    port = int(os.environ.get("PORT", "0") or 0)
    if port:
        from .api import start_api_server
        start_api_server(trader, _now(), port)
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    import sys
    main(place_orders="--live-demo" in sys.argv)

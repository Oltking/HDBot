"""Deriv NEW trading API client (2026 architecture) for authenticated actions.

Auth flow (see slp-strategy-spec memory):
  1. REST GET  /trading/v1/options/accounts        (Bearer PAT + Deriv-App-ID)
  2. REST POST /trading/v1/options/accounts/{id}/otp -> single-use wss URL (120s)
  3. Connect that wss URL -> authenticated session (no further authorize needed)

The WS message protocol mirrors the legacy API (proposal/buy/sell with echo_req),
EXCEPT the instrument field is `underlying_symbol` (not `symbol`). Multipliers are
supported: contract_type MULTUP/MULTDOWN, `multiplier`, and `limit_order` with
stop_loss/take_profit (money amounts).

Market DATA still comes from the legacy unauthenticated endpoint (slp/deriv.py);
this module is only for account + order actions.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

from . import config

REST_BASE = "https://api.derivws.com"


class DerivV2Error(RuntimeError):
    pass


class DerivV2:
    def __init__(self, app_id: str | None = None, token: str | None = None):
        self.app_id = app_id or config.DERIV_APP_ID
        self.token = token or config.deriv_token()
        if not self.token:
            raise DerivV2Error("No DERIV_API_TOKEN configured.")
        self.ws = None
        self.account: dict | None = None

    # ---- REST auth -------------------------------------------------------
    def _rest(self, method: str, path: str) -> dict:
        req = urllib.request.Request(
            REST_BASE + path, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Deriv-App-ID": self.app_id})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise DerivV2Error(f"{method} {path} -> HTTP {e.code}: {e.read()[:200]!r}")

    def accounts(self) -> list[dict]:
        return self._rest("GET", "/trading/v1/options/accounts")["data"]

    def pick_demo(self) -> dict:
        for a in self.accounts():
            if a.get("account_type") == "demo":
                return a
        raise DerivV2Error("No demo account found for this token.")

    def _otp_url(self, account_id: str) -> str:
        return self._rest(
            "POST", f"/trading/v1/options/accounts/{account_id}/otp")["data"]["url"]

    # ---- WS session ------------------------------------------------------
    async def connect(self, demo_only: bool = True) -> dict:
        """Authenticate and open the trading WS. Returns the chosen account."""
        self.account = self.pick_demo() if demo_only else self.accounts()[0]
        await self._open_ws()
        return self.account

    async def _open_ws(self) -> None:
        """(Re)mint an OTP and open a fresh authenticated WS for self.account."""
        assert self.account is not None
        url = self._otp_url(self.account["account_id"])
        self.ws = await websockets.connect(url)

    async def _call(self, msg: dict, timeout: float = 10.0, _retry: bool = True) -> dict:
        """Send a request and await its reply, transparently reconnecting once if
        the socket has dropped (OTP-authenticated sockets die when idle/over time)."""
        if self.ws is None:
            await self._open_ws()
        want = next(iter(msg))
        try:
            await self.ws.send(json.dumps(msg))
            while True:
                resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
                if resp.get("msg_type") in (want, "error") or want in resp:
                    if "error" in resp:
                        raise DerivV2Error(resp["error"].get("message", "unknown error"))
                    return resp
        except (websockets.ConnectionClosed, asyncio.TimeoutError, OSError) as e:
            if not _retry:
                raise DerivV2Error(f"WS call failed: {e}")
            # Reconnect with a fresh OTP and try the call one more time.
            await self._open_ws()
            return await self._call(msg, timeout, _retry=False)

    async def ping(self) -> None:
        """Keepalive; reconnects if the socket has gone away."""
        await self._call({"ping": 1}, timeout=8)

    async def multipliers_for(self, underlying: str, direction_up: bool) -> list[int]:
        r = await self._call({"contracts_for": underlying})
        ct = "MULTUP" if direction_up else "MULTDOWN"
        for a in r["contracts_for"]["available"]:
            if a.get("contract_type") == ct and a.get("multiplier_range"):
                return list(a["multiplier_range"])
        return []

    async def proposal(self, underlying: str, direction_up: bool, stake: float,
                       multiplier: int, stop_loss: float, take_profit: float) -> dict:
        return (await self._call({
            "proposal": 1, "amount": round(stake, 2), "basis": "stake",
            "contract_type": "MULTUP" if direction_up else "MULTDOWN",
            "currency": "USD", "multiplier": multiplier,
            "underlying_symbol": underlying,
            "limit_order": {"stop_loss": round(stop_loss, 2),
                            "take_profit": round(take_profit, 2)},
        }))["proposal"]

    async def buy(self, proposal_id: str, max_price: float) -> dict:
        return (await self._call({"buy": proposal_id, "price": round(max_price, 2)}))["buy"]

    async def sell(self, contract_id: str) -> dict:
        return (await self._call({"sell": contract_id, "price": 0}))["sell"]

    async def open_contract(self, contract_id: str) -> dict:
        return (await self._call(
            {"proposal_open_contract": 1, "contract_id": contract_id}))["proposal_open_contract"]

    async def balance(self) -> float:
        # account balance from the REST accounts list (fast, no WS needed)
        return float(self.pick_demo()["balance"])

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

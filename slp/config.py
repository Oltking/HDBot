"""Config + secrets loading.

Reads a local `.env` (never committed) and the process environment. The Deriv
API token lives ONLY here at runtime — it is never hardcoded, logged, or passed
around in plaintext beyond the WebSocket auth call.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")


def deriv_token() -> str | None:
    """The Deriv API token, or None if not configured (history needs no token)."""
    return os.environ.get("DERIV_API_TOKEN")

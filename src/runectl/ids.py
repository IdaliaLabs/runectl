"""Sortable run-id generation (D3)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_run_id(now: datetime | None = None) -> str:
    """Return a lexicographically sortable run id: YYYYMMDD-HHMMSS-<6hex>."""
    moment = now or datetime.now(UTC)
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"

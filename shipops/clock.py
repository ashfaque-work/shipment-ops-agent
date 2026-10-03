"""Application clock. Shares MOCK_CLOCK with the mock carrier so prompts stay deterministic."""

from __future__ import annotations

import os
from datetime import UTC, datetime

DEFAULT_CLOCK = "2026-10-01T09:00:00+00:00"


def now() -> datetime:
    raw = os.getenv("MOCK_CLOCK", DEFAULT_CLOCK).strip()
    if raw.lower() == "now":
        return datetime.now(UTC).replace(microsecond=0)
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

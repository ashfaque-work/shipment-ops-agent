"""Reference clock for the mock carrier.

All scenario timestamps are expressed relative to this clock, so the service is fully
deterministic by default (evals and recorded LLM responses depend on that). Set
MOCK_CLOCK=now to anchor scenarios to the real current time for live demos.
"""

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

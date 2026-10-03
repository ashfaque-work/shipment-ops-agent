"""Carrier adapters. Use `get_carrier()` to obtain the one selected by CARRIER=mock|fedex."""

from __future__ import annotations

from shipops.carriers.base import (
    CarrierAdapter,
    CarrierError,
    CarrierInvalidRequest,
    CarrierNotFound,
    CarrierTimeout,
    CarrierUnavailable,
)
from shipops.config import Settings, get_settings


def get_carrier(settings: Settings | None = None) -> CarrierAdapter:
    s = settings or get_settings()
    if s.demo_mode or s.carrier == "mock":
        from shipops.carriers.mock import MockCarrierAdapter

        return MockCarrierAdapter(
            s.mock_carrier_url, timeout_s=s.carrier_timeout_s, max_retries=s.carrier_max_retries
        )
    if s.carrier == "fedex":
        from shipops.carriers.fedex import FedExSandboxAdapter

        return FedExSandboxAdapter()
    raise ValueError(f"Unknown CARRIER={s.carrier!r}")


__all__ = [
    "CarrierAdapter",
    "CarrierError",
    "CarrierInvalidRequest",
    "CarrierNotFound",
    "CarrierTimeout",
    "CarrierUnavailable",
    "get_carrier",
]

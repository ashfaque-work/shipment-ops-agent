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

        if s.mock_carrier_url == "inprocess":
            # Single-service deploys: run the mock carrier inside this process.
            from fastapi.testclient import TestClient

            from mock_carrier.app import create_app

            client = TestClient(create_app(), base_url="http://mock-carrier")
            return MockCarrierAdapter(client=client, max_retries=s.carrier_max_retries)
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

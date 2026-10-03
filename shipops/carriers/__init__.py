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

        if s.demo_mode or s.mock_carrier_url == "inprocess":
            # Demo / single-service deploys: the mock carrier runs inside this process, so
            # a free host needs one web service. The simulated outage stalls only as long
            # as a real client timeout would (an in-process client can't time out itself).
            from fastapi.testclient import TestClient

            from mock_carrier.app import create_app

            app = create_app(timeout_delay_s=s.carrier_timeout_s)
            client = TestClient(app, base_url="http://mock-carrier")
            return MockCarrierAdapter(
                client=client, max_retries=s.carrier_max_retries, backoff_s=0.3
            )
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

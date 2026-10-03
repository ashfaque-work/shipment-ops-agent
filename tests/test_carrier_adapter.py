from __future__ import annotations

import httpx
import pytest

from shipops.carriers import get_carrier
from shipops.carriers.base import (
    CarrierInvalidRequest,
    CarrierNotFound,
    CarrierTimeout,
    CarrierUnavailable,
)
from shipops.carriers.fedex import FedExSandboxAdapter
from shipops.carriers.mock import MockCarrierAdapter
from shipops.carriers.models import Address, Location, RateRequest
from shipops.config import Settings


def test_track_ok(carrier: MockCarrierAdapter) -> None:
    info = carrier.track("mc100004518")
    assert info.status == "customs_hold"
    assert info.exception and info.exception.code == "CUSTOMS_DOCS_REQUIRED"


def test_track_not_found_is_not_retried(carrier: MockCarrierAdapter) -> None:
    with pytest.raises(CarrierNotFound):
        carrier.track("MC199999999")
    assert len(carrier.calls) == 1


def test_flaky_upstream_recovers_with_retry(carrier: MockCarrierAdapter) -> None:
    info = carrier.track("MC100000503")
    assert info.status == "in_transit"
    assert len(carrier.calls) == 2  # 503, then success


def test_timeout_scenario_exhausts_retries(carrier: MockCarrierAdapter) -> None:
    with pytest.raises(CarrierTimeout):
        carrier.track("MC100000408")
    assert len(carrier.calls) == 2  # timeouts are retried only once


def test_client_side_timeout_maps_to_carrier_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    adapter = MockCarrierAdapter(
        client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler)),
        max_retries=1,
        backoff_s=0,
    )
    with pytest.raises(CarrierTimeout):
        adapter.track("MC100004512")
    assert len(adapter.calls) == 2


def test_connection_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    adapter = MockCarrierAdapter(
        client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    with pytest.raises(CarrierUnavailable):
        adapter.track("MC100004512")


def test_rates_and_invalid_location(carrier: MockCarrierAdapter) -> None:
    res = carrier.get_rates(
        RateRequest(
            origin=Location(city="Kolkata", country="IN"),
            destination=Location(city="Nairobi", country="KE"),
            weight_kg=2,
        )
    )
    assert [q.service_code for q in res.quotes][0] == "ECONOMY"
    with pytest.raises(CarrierInvalidRequest):
        carrier.get_rates(
            RateRequest(
                origin=Location(city="Paris", country="FR"),
                destination=Location(city="Lagos", country="NG"),
                weight_kg=2,
            )
        )


def test_validate_address(carrier: MockCarrierAdapter) -> None:
    res = carrier.validate_address(Address(street1="Opebi Road", city="Lagos", country="NG"))
    assert not res.valid


def test_create_shipment_not_retried_on_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "x", "message": "down"})

    adapter = MockCarrierAdapter(
        client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler)),
        max_retries=3,
        backoff_s=0,
    )
    from shipops.agent.tools import build_shipment_request

    req = build_shipment_request({"order_id": "4550", "service_code": "STANDARD"})
    with pytest.raises(CarrierUnavailable):
        adapter.create_shipment(req)
    assert len(adapter.calls) == 1


def test_factory_selects_adapter() -> None:
    assert isinstance(get_carrier(Settings(carrier="mock")), MockCarrierAdapter)
    assert isinstance(get_carrier(Settings(carrier="fedex")), FedExSandboxAdapter)
    # Demo mode forces the mock carrier regardless of CARRIER.
    assert isinstance(get_carrier(Settings(carrier="fedex", demo_mode=True)), MockCarrierAdapter)


def test_fedex_stub_raises() -> None:
    with pytest.raises(NotImplementedError):
        FedExSandboxAdapter().track("123")

"""Adapter for the bundled mock carrier service (default, CARRIER=mock)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from shipops.carriers.base import (
    CarrierAdapter,
    CarrierError,
    CarrierInvalidRequest,
    CarrierNotFound,
    CarrierTimeout,
    CarrierUnavailable,
)
from shipops.carriers.models import (
    Address,
    AddressValidation,
    RateRequest,
    RateResult,
    ShipmentConfirmation,
    ShipmentRequest,
    TrackingInfo,
)

log = logging.getLogger(__name__)


class MockCarrierAdapter(CarrierAdapter):
    name = "mock"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8001",
        *,
        timeout_s: float = 3.0,
        max_retries: int = 2,
        backoff_s: float = 0.3,
        client: httpx.Client | None = None,
    ):
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.calls: list[tuple[str, str]] = []  # (method, path) audit log, used by safety evals

    # -- transport ---------------------------------------------------------------

    def _request(self, method: str, path: str, *, json: Any = None, retry: bool = True) -> Any:
        attempts = 1 + (self.max_retries if retry else 0)
        last: CarrierError | None = None
        timeouts = 0
        for attempt in range(attempts):
            # A hung upstream rarely recovers within seconds: retry a timeout only once so
            # the user is not kept waiting for (1 + max_retries) x timeout.
            if timeouts >= 2:
                break
            if attempt:
                time.sleep(self.backoff_s * 2 ** (attempt - 1))
            self.calls.append((method, path))
            try:
                resp = self._client.request(method, path, json=json)
            except httpx.TimeoutException:
                timeouts += 1
                last = CarrierTimeout("The carrier did not respond in time")
                log.warning("carrier timeout %s %s (attempt %d)", method, path, attempt + 1)
                continue
            except httpx.TransportError as exc:
                last = CarrierUnavailable(f"Could not reach the carrier: {exc.__class__.__name__}")
                continue
            if resp.status_code < 400:
                return resp.json()
            err = self._map_error(resp)
            if not err.retryable:
                raise err
            last = err
            if isinstance(err, CarrierTimeout):
                timeouts += 1
            log.warning(
                "carrier %s on %s %s (attempt %d)", resp.status_code, method, path, attempt + 1
            )
        assert last is not None
        raise last

    @staticmethod
    def _map_error(resp: httpx.Response) -> CarrierError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        msg = body.get("message") or body.get("detail") or resp.text[:200]
        if not isinstance(msg, str):
            msg = str(msg)
        if resp.status_code == 404:
            return CarrierNotFound(msg, details=body)
        if resp.status_code in (400, 409, 422):
            return CarrierInvalidRequest(msg, details=body)
        if resp.status_code in (408, 504):
            return CarrierTimeout(msg, details=body)
        if resp.status_code in (429, 500, 502, 503):
            return CarrierUnavailable(msg, details=body)
        return CarrierError(msg, details=body)

    # -- interface ---------------------------------------------------------------

    def track(self, tracking_number: str) -> TrackingInfo:
        tn = tracking_number.strip().upper()
        return TrackingInfo.model_validate(self._request("GET", f"/track/{tn}"))

    def get_rates(self, request: RateRequest) -> RateResult:
        return RateResult.model_validate(
            self._request("POST", "/rates", json=request.model_dump(mode="json"))
        )

    def validate_address(self, address: Address) -> AddressValidation:
        payload = address.model_dump(mode="json", exclude={"email"})
        return AddressValidation.model_validate(
            self._request("POST", "/address/validate", json=payload)
        )

    def create_shipment(self, request: ShipmentRequest) -> ShipmentConfirmation:
        payload = request.model_dump(mode="json")
        for party in ("shipper", "recipient"):
            payload[party].pop("email", None)
        # Never auto-retry a write: a timeout does not tell us whether the label was created.
        return ShipmentConfirmation.model_validate(
            self._request("POST", "/shipments", json=payload, retry=False)
        )

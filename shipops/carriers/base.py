"""CarrierAdapter interface and the error taxonomy every adapter must map onto."""

from __future__ import annotations

from abc import ABC, abstractmethod

from shipops.carriers.models import (
    Address,
    AddressValidation,
    RateRequest,
    RateResult,
    ShipmentConfirmation,
    ShipmentRequest,
    TrackingInfo,
)


class CarrierError(Exception):
    """Base carrier failure. `code` is stable and safe to show to the LLM / user."""

    code = "carrier_error"
    retryable = False

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class CarrierNotFound(CarrierError):
    code = "not_found"


class CarrierInvalidRequest(CarrierError):
    code = "invalid_request"


class CarrierTimeout(CarrierError):
    code = "carrier_timeout"
    retryable = True


class CarrierUnavailable(CarrierError):
    code = "carrier_unavailable"
    retryable = True


class CarrierAdapter(ABC):
    """Uniform interface over shipping carriers (mock, FedEx sandbox, ...)."""

    name: str = "abstract"

    @abstractmethod
    def track(self, tracking_number: str) -> TrackingInfo: ...

    @abstractmethod
    def get_rates(self, request: RateRequest) -> RateResult: ...

    @abstractmethod
    def create_shipment(self, request: ShipmentRequest) -> ShipmentConfirmation:
        """Irreversible (creates a billable label). Callers must obtain human confirmation."""

    @abstractmethod
    def validate_address(self, address: Address) -> AddressValidation: ...

"""FedEx sandbox adapter — STUB (CARRIER=fedex).

This file documents how a real carrier plugs into the CarrierAdapter interface. It makes
no network calls and needs no credentials; every method raises NotImplementedError.

To implement (requires a free FedEx Developer account — ask the project owner first,
never commit credentials):

1. Create a project at https://developer.fedex.com/ and get *sandbox* credentials
   (API key + secret key) and a test account number.
2. Add to .env (never commit):  FEDEX_API_KEY, FEDEX_SECRET_KEY, FEDEX_ACCOUNT_NUMBER,
   FEDEX_BASE_URL=https://apis-sandbox.fedex.com
3. Auth: POST /oauth/token (grant_type=client_credentials) -> bearer token, valid ~60 min.
   Cache it and refresh on 401.
4. Map each method below onto the FedEx REST API and translate responses into the
   domain models in shipops/carriers/models.py. Map errors onto the CarrierError
   taxonomy in shipops/carriers/base.py (404 -> CarrierNotFound, 5xx -> CarrierUnavailable,
   timeouts -> CarrierTimeout, validation -> CarrierInvalidRequest).
5. Add contract tests using recorded sandbox responses (no live calls in CI).
"""

from __future__ import annotations

from shipops.carriers.base import CarrierAdapter
from shipops.carriers.models import (
    Address,
    AddressValidation,
    RateRequest,
    RateResult,
    ShipmentConfirmation,
    ShipmentRequest,
    TrackingInfo,
)

_MSG = "FedExSandboxAdapter is a stub. See shipops/carriers/fedex.py for implementation steps."


class FedExSandboxAdapter(CarrierAdapter):
    name = "fedex"

    def __init__(self, api_key: str = "", secret_key: str = "", account_number: str = ""):
        # TODO: store credentials; lazily fetch OAuth token from /oauth/token.
        self._configured = bool(api_key and secret_key and account_number)

    def track(self, tracking_number: str) -> TrackingInfo:
        # TODO: POST /track/v1/trackingnumbers
        #   body: {"includeDetailedScans": true,
        #          "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tracking_number}}]}
        #   map output.completeTrackResults[0].trackResults[0]:
        #     latestStatusDetail.code/description -> status/status_detail
        #     scanEvents[] -> events, estimatedDeliveryTimeWindow -> estimated_delivery
        raise NotImplementedError(_MSG)

    def get_rates(self, request: RateRequest) -> RateResult:
        # TODO: POST /rate/v1/rates/quotes
        #   requestedShipment.shipper/recipient.address{city, countryCode, postalCode},
        #   requestedPackageLineItems[{weight{units: KG, value}, dimensions{units: CM}}]
        #   map rateReplyDetails[] -> RateQuote(service_code=serviceType, price=totalNetCharge)
        raise NotImplementedError(_MSG)

    def create_shipment(self, request: ShipmentRequest) -> ShipmentConfirmation:
        # TODO: POST /ship/v1/shipments (sandbox labels are not billable).
        #   Always send an idempotency/customer reference; never auto-retry on timeout.
        raise NotImplementedError(_MSG)

    def validate_address(self, address: Address) -> AddressValidation:
        # TODO: POST /address/v1/addresses/resolve
        #   map resolvedAddresses[0].customerMessages / attributes -> issues, suggestions
        raise NotImplementedError(_MSG)

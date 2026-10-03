"""Mock carrier HTTP service.

Run:  uvicorn mock_carrier.app:app --port 8001
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mock_carrier import address as address_rules
from mock_carrier import clock, rates
from mock_carrier.geo import find_city
from mock_carrier.models import (
    Address,
    AddressValidationResponse,
    ErrorResponse,
    RateRequest,
    RateResponse,
    ShipmentRequest,
    ShipmentResponse,
    ShipmentStatus,
    TrackingEvent,
    TrackingResponse,
)
from mock_carrier.scenarios import FLAKY_TRACKING, SCENARIOS, TIMEOUT_TRACKING


class CarrierError(Exception):
    def __init__(self, status: int, error: str, message: str, extra: dict | None = None):
        self.status, self.error, self.message, self.extra = status, error, message, extra or {}


class _State:
    """In-memory mutable state (created shipments, flaky counter). Reset via /_admin/reset."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.created: dict[str, tuple[ShipmentRequest, ShipmentResponse]] = {}
        self.flaky_calls = 0


def _resolve_city(loc_city: str, loc_country: str, field: str):
    city = find_city(loc_city, loc_country)
    if city is None:
        raise CarrierError(
            422,
            "unsupported_location",
            f"{field}: '{loc_city}, {loc_country}' is not in the service area",
        )
    return city


def create_app(*, timeout_delay_s: float | None = None) -> FastAPI:
    """`timeout_delay_s` overrides MOCK_TIMEOUT_DELAY_S (how long the simulated carrier
    outage stalls). In-process callers set it to their own timeout, since an in-process
    client cannot time out on its own."""
    app = FastAPI(
        title="Mock Carrier API",
        version="1.0.0",
        description="Deterministic fake carrier used for development, demos and evals.",
    )
    state = _State()
    app.state.carrier = state

    @app.exception_handler(CarrierError)
    async def _carrier_error(_: Request, exc: CarrierError) -> JSONResponse:
        body = ErrorResponse(error=exc.error, message=exc.message).model_dump() | exc.extra
        return JSONResponse(status_code=exc.status, content=body)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "clock": clock.now().isoformat()}

    @app.get(
        "/track/{tracking_number}",
        response_model=TrackingResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    async def track(tracking_number: str) -> TrackingResponse:
        tn = tracking_number.strip().upper()
        if tn == TIMEOUT_TRACKING:
            # Simulate an upstream that hangs; clients must time out and degrade gracefully.
            delay = timeout_delay_s
            if delay is None:
                delay = float(os.getenv("MOCK_TIMEOUT_DELAY_S", "10"))
            await asyncio.sleep(delay)
            raise CarrierError(504, "upstream_timeout", "Carrier tracking backend timed out")
        if tn == FLAKY_TRACKING:
            with state.lock:
                state.flaky_calls += 1
                fail = state.flaky_calls % 2 == 1
            if fail:
                raise CarrierError(503, "service_unavailable", "Temporary carrier outage")
        if tn in SCENARIOS:
            return SCENARIOS[tn].render(clock.now())
        if tn in state.created:
            req, resp = state.created[tn]
            return TrackingResponse(
                tracking_number=tn,
                status=ShipmentStatus.LABEL_CREATED,
                status_detail="Shipment information received; awaiting pickup",
                service_code=resp.service_code,
                origin=f"{req.shipper.city}, {req.shipper.country}",
                destination=f"{req.recipient.city}, {req.recipient.country}",
                current_location=f"{req.shipper.city}, {req.shipper.country}",
                weight_kg=req.package.weight_kg,
                estimated_delivery=None,
                events=[
                    TrackingEvent(
                        timestamp=resp.created_at,
                        location=f"{req.shipper.city}, {req.shipper.country}",
                        code="LC",
                        description="Shipping label created",
                    )
                ],
            )
        raise CarrierError(404, "not_found", f"No shipment found for tracking number {tn}")

    @app.post("/rates", response_model=RateResponse, responses={422: {"model": ErrorResponse}})
    async def get_rates(req: RateRequest) -> RateResponse:
        o = _resolve_city(req.origin.city, req.origin.country, "origin")
        d = _resolve_city(req.destination.city, req.destination.country, "destination")
        if o == d:
            raise CarrierError(422, "same_location", "Origin and destination are the same city")
        distance, vol, quotes = rates.quote(o, d, req.weight_kg, req.dimensions, clock.now())
        return RateResponse(
            origin=f"{o.name}, {o.country}",
            destination=f"{d.name}, {d.country}",
            distance_km=distance,
            actual_weight_kg=req.weight_kg,
            volumetric_weight_kg=vol,
            quotes=quotes,
        )

    @app.post("/address/validate", response_model=AddressValidationResponse)
    async def validate_address(addr: Address) -> AddressValidationResponse:
        return address_rules.validate(addr)

    @app.post(
        "/shipments",
        response_model=ShipmentResponse,
        status_code=201,
        responses={422: {"model": ErrorResponse}},
    )
    async def create_shipment(req: ShipmentRequest) -> ShipmentResponse:
        for label, addr in (("shipper", req.shipper), ("recipient", req.recipient)):
            check = address_rules.validate(addr)
            if not check.valid:
                raise CarrierError(
                    422,
                    "invalid_address",
                    f"{label} address is invalid: " + "; ".join(check.issues),
                    {"field": label, "issues": check.issues},
                )
        o = find_city(req.shipper.city, req.shipper.country)
        d = find_city(req.recipient.city, req.recipient.country)
        assert o is not None and d is not None  # guaranteed by validation above
        now = clock.now()
        _, _, quotes = rates.quote(o, d, req.package.weight_kg, req.package.dimensions, now)
        chosen = next((q for q in quotes if q.service_code == req.service_code.upper()), None)
        if chosen is None:
            available = [q.service_code for q in quotes]
            raise CarrierError(
                422,
                "service_unavailable",
                f"Service '{req.service_code}' not available for this shipment "
                f"(available: {', '.join(available)})",
            )
        # Deterministic + idempotent: identical requests yield the same tracking number.
        digest = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
        tn = "MC2" + str(int(digest[:12], 16) % 10**8).zfill(8)
        with state.lock:
            if tn in state.created:
                return state.created[tn][1]
            resp = ShipmentResponse(
                tracking_number=tn,
                status=ShipmentStatus.LABEL_CREATED,
                service_code=chosen.service_code,
                price=chosen.price,
                currency=chosen.currency,
                estimated_delivery=(now + timedelta(days=chosen.transit_days_max)).date(),
                label_url=f"https://mock-carrier.example/labels/{tn}.pdf",
                created_at=now,
                reference=req.reference,
            )
            state.created[tn] = (req, resp)
        return resp

    @app.post("/_admin/reset", include_in_schema=False)
    async def reset() -> dict:
        with state.lock:
            state.reset()
        return {"status": "reset"}

    return app


app = create_app()

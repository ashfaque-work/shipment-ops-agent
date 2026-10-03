"""Deterministic seeded tracking scenarios.

Every timestamp is an offset from the reference clock (see clock.py). Special tracking
numbers simulate infrastructure failures:

* TIMEOUT_TRACKING  - the endpoint stalls longer than any sane client timeout
* FLAKY_TRACKING    - every odd-numbered request returns HTTP 503 (retry succeeds)
* anything unknown  - HTTP 404
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from mock_carrier.models import (
    ShipmentException,
    ShipmentStatus,
    TrackingEvent,
    TrackingResponse,
)

TIMEOUT_TRACKING = "MC100000408"
FLAKY_TRACKING = "MC100000503"


@dataclass(frozen=True)
class Ev:
    """Scenario event: offset (hours relative to the clock; negative = past)."""

    hours: float
    location: str
    code: str
    description: str


@dataclass(frozen=True)
class Scenario:
    tracking_number: str
    status: ShipmentStatus
    status_detail: str
    service_code: str
    origin: str
    destination: str
    current_location: str
    weight_kg: float
    eta_hours: float | None
    events: list[Ev]
    original_eta_hours: float | None = None
    exception: ShipmentException | None = None
    tags: list[str] = field(default_factory=list)

    def render(self, now: datetime) -> TrackingResponse:
        def at(h: float) -> datetime:
            return now + timedelta(hours=h)

        events = sorted(self.events, key=lambda e: e.hours, reverse=True)
        delivered_at = at(events[0].hours) if self.status == ShipmentStatus.DELIVERED else None
        return TrackingResponse(
            tracking_number=self.tracking_number,
            status=self.status,
            status_detail=self.status_detail,
            service_code=self.service_code,
            origin=self.origin,
            destination=self.destination,
            current_location=self.current_location,
            weight_kg=self.weight_kg,
            estimated_delivery=at(self.eta_hours) if self.eta_hours is not None else None,
            original_estimated_delivery=(
                at(self.original_eta_hours) if self.original_eta_hours is not None else None
            ),
            delivered_at=delivered_at,
            exception=self.exception,
            events=[
                TrackingEvent(
                    timestamp=at(e.hours),
                    location=e.location,
                    code=e.code,
                    description=e.description,
                )
                for e in events
            ],
        )


D = 24  # hours per day, for readability

SCENARIOS: dict[str, Scenario] = {
    s.tracking_number: s
    for s in [
        # --- Normal in-transit: Mumbai -> Lagos (order 4512) ---
        Scenario(
            tracking_number="MC100004512",
            status=ShipmentStatus.IN_TRANSIT,
            status_detail="Arrived at destination hub; customs cleared",
            service_code="STANDARD",
            origin="Mumbai, IN",
            destination="Lagos, NG",
            current_location="Lagos Hub, NG",
            weight_kg=12.4,
            eta_hours=2 * D,
            events=[
                Ev(-6 * D, "Mumbai, IN", "PU", "Picked up from shipper"),
                Ev(-5.8 * D, "Mumbai Gateway, IN", "DP", "Departed origin facility"),
                Ev(-4 * D, "Dubai Hub, AE", "AR", "Arrived at transit hub"),
                Ev(-3.6 * D, "Dubai Hub, AE", "DP", "Departed transit hub"),
                Ev(-2 * D, "Lagos Hub, NG", "AR", "Arrived at destination hub"),
                Ev(-1 * D, "Lagos Hub, NG", "CC", "Cleared customs"),
            ],
            tags=["normal", "in_transit"],
        ),
        # --- Delivered: Kolkata -> Nairobi (order 4513) ---
        Scenario(
            tracking_number="MC100004513",
            status=ShipmentStatus.DELIVERED,
            status_detail="Delivered - signed for by W. KAMAU",
            service_code="EXPRESS",
            origin="Kolkata, IN",
            destination="Nairobi, KE",
            current_location="Nairobi, KE",
            weight_kg=3.2,
            eta_hours=None,
            events=[
                Ev(-5 * D, "Kolkata, IN", "PU", "Picked up from shipper"),
                Ev(-4.7 * D, "Kolkata Gateway, IN", "DP", "Departed origin facility"),
                Ev(-3.5 * D, "Nairobi Hub, KE", "AR", "Arrived at destination hub"),
                Ev(-3 * D, "Nairobi Hub, KE", "CC", "Cleared customs"),
                Ev(-2.2 * D, "Nairobi, KE", "OD", "Out for delivery"),
                Ev(-2 * D, "Nairobi, KE", "DL", "Delivered - signed for by W. KAMAU"),
            ],
            tags=["normal", "delivered"],
        ),
        # --- Held at customs awaiting documents: Delhi -> Accra (order 4518) ---
        Scenario(
            tracking_number="MC100004518",
            status=ShipmentStatus.CUSTOMS_HOLD,
            status_detail="Held by Ghana Revenue Authority customs - documents required",
            service_code="STANDARD",
            origin="Delhi, IN",
            destination="Accra, GH",
            current_location="Kotoka Intl Airport Customs, Accra, GH",
            weight_kg=18.0,
            eta_hours=None,
            original_eta_hours=-1 * D,
            exception=ShipmentException(
                code="CUSTOMS_DOCS_REQUIRED",
                reason="Commercial invoice is missing the HS code for line 2 and the "
                "importer's TIN is not provided.",
                action_required="Shipper must upload a corrected commercial invoice "
                "(with HS codes) and the consignee's TIN within 5 days to avoid return.",
            ),
            events=[
                Ev(-7 * D, "Delhi, IN", "PU", "Picked up from shipper"),
                Ev(-6.6 * D, "Delhi Gateway, IN", "DP", "Departed origin facility"),
                Ev(-4 * D, "Accra Hub, GH", "AR", "Arrived at destination hub"),
                Ev(
                    -3.8 * D,
                    "Kotoka Intl Airport Customs, Accra, GH",
                    "CH",
                    "Held by customs - commercial invoice incomplete",
                ),
                Ev(
                    -1 * D,
                    "Kotoka Intl Airport Customs, Accra, GH",
                    "CH",
                    "Awaiting documents from shipper",
                ),
            ],
            tags=["exception", "customs"],
        ),
        # --- Weather delay with revised ETA: Dubai -> Johannesburg (order 4521) ---
        Scenario(
            tracking_number="MC100004521",
            status=ShipmentStatus.DELAYED,
            status_detail="Delayed - severe weather at transit point",
            service_code="EXPRESS",
            origin="Dubai, AE",
            destination="Johannesburg, ZA",
            current_location="Dubai Hub, AE",
            weight_kg=7.5,
            eta_hours=4 * D,
            original_eta_hours=1 * D,
            exception=ShipmentException(
                code="WEATHER_DELAY",
                reason="Flights out of Dubai suspended due to a severe sandstorm.",
                action_required="No action needed. Shipment will move on the next "
                "available flight; revised ETA applies.",
            ),
            events=[
                Ev(-2 * D, "Dubai, AE", "PU", "Picked up from shipper"),
                Ev(-1.8 * D, "Dubai Hub, AE", "AR", "Arrived at origin facility"),
                Ev(-1 * D, "Dubai Hub, AE", "WX", "Flight cancelled - severe weather"),
                Ev(-0.2 * D, "Dubai Hub, AE", "RE", "Delivery date revised"),
            ],
            tags=["exception", "weather"],
        ),
        # --- Failed delivery, address problem: Mumbai -> Lagos (order 4527) ---
        Scenario(
            tracking_number="MC100004527",
            status=ShipmentStatus.DELIVERY_FAILED,
            status_detail="Delivery attempt failed - address incomplete",
            service_code="STANDARD",
            origin="Mumbai, IN",
            destination="Lagos, NG",
            current_location="Lagos Ikeja Delivery Station, NG",
            weight_kg=4.1,
            eta_hours=None,
            original_eta_hours=-1 * D,
            exception=ShipmentException(
                code="ADDRESS_INCOMPLETE",
                reason="Courier could not locate the premises: no building number "
                "and the recipient phone was unreachable.",
                action_required="Provide a corrected address (building number / landmark) "
                "and a reachable phone number within 3 days, or the parcel is returned.",
            ),
            events=[
                Ev(-8 * D, "Mumbai, IN", "PU", "Picked up from shipper"),
                Ev(-7.7 * D, "Mumbai Gateway, IN", "DP", "Departed origin facility"),
                Ev(-4 * D, "Lagos Hub, NG", "AR", "Arrived at destination hub"),
                Ev(-3 * D, "Lagos Hub, NG", "CC", "Cleared customs"),
                Ev(-1.3 * D, "Lagos Ikeja Delivery Station, NG", "OD", "Out for delivery"),
                Ev(
                    -1 * D,
                    "Lagos Ikeja Delivery Station, NG",
                    "DF",
                    "Delivery attempt failed - address incomplete, recipient unreachable",
                ),
            ],
            tags=["exception", "address"],
        ),
        # --- Early in transit: Kolkata -> Nairobi (order 4530) ---
        Scenario(
            tracking_number="MC100004530",
            status=ShipmentStatus.IN_TRANSIT,
            status_detail="Departed origin facility",
            service_code="ECONOMY",
            origin="Kolkata, IN",
            destination="Nairobi, KE",
            current_location="Kolkata Gateway, IN",
            weight_kg=22.0,
            eta_hours=9 * D,
            events=[
                Ev(-1.5 * D, "Kolkata, IN", "PU", "Picked up from shipper"),
                Ev(-1 * D, "Kolkata Gateway, IN", "DP", "Departed origin facility"),
            ],
            tags=["normal", "in_transit"],
        ),
        # --- Out for delivery: Delhi -> Accra (order 4533) ---
        Scenario(
            tracking_number="MC100004533",
            status=ShipmentStatus.OUT_FOR_DELIVERY,
            status_detail="Out for delivery",
            service_code="EXPRESS",
            origin="Delhi, IN",
            destination="Accra, GH",
            current_location="Accra Delivery Station, GH",
            weight_kg=2.3,
            eta_hours=8,
            events=[
                Ev(-4 * D, "Delhi, IN", "PU", "Picked up from shipper"),
                Ev(-3.7 * D, "Delhi Gateway, IN", "DP", "Departed origin facility"),
                Ev(-2 * D, "Accra Hub, GH", "AR", "Arrived at destination hub"),
                Ev(-1 * D, "Accra Hub, GH", "CC", "Cleared customs"),
                Ev(-2, "Accra Delivery Station, GH", "OD", "Out for delivery"),
            ],
            tags=["normal", "out_for_delivery"],
        ),
        # --- Label created, awaiting pickup: Dubai -> Johannesburg (order 4536) ---
        Scenario(
            tracking_number="MC100004536",
            status=ShipmentStatus.LABEL_CREATED,
            status_detail="Shipment information received; awaiting pickup",
            service_code="STANDARD",
            origin="Dubai, AE",
            destination="Johannesburg, ZA",
            current_location="Dubai, AE",
            weight_kg=9.8,
            eta_hours=7 * D,
            events=[
                Ev(-3, "Dubai, AE", "LC", "Shipping label created"),
            ],
            tags=["normal", "label_created"],
        ),
        # --- Flaky upstream (503 on odd calls), otherwise normal: Kolkata -> Nairobi ---
        Scenario(
            tracking_number=FLAKY_TRACKING,
            status=ShipmentStatus.IN_TRANSIT,
            status_detail="In transit to destination hub",
            service_code="STANDARD",
            origin="Kolkata, IN",
            destination="Nairobi, KE",
            current_location="Dubai Hub, AE",
            weight_kg=6.0,
            eta_hours=3 * D,
            events=[
                Ev(-3 * D, "Kolkata, IN", "PU", "Picked up from shipper"),
                Ev(-2.6 * D, "Kolkata Gateway, IN", "DP", "Departed origin facility"),
                Ev(-1 * D, "Dubai Hub, AE", "AR", "Arrived at transit hub"),
            ],
            tags=["infra", "flaky"],
        ),
    ]
}

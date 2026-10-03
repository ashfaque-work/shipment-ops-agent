"""Pydantic models for the mock carrier API (request/response contracts)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ShipmentStatus(StrEnum):
    LABEL_CREATED = "label_created"
    IN_TRANSIT = "in_transit"
    CUSTOMS_HOLD = "customs_hold"
    DELAYED = "delayed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERED = "delivered"


class TrackingEvent(BaseModel):
    timestamp: datetime
    location: str
    code: str
    description: str


class ShipmentException(BaseModel):
    code: str
    reason: str
    action_required: str


class TrackingResponse(BaseModel):
    tracking_number: str
    status: ShipmentStatus
    status_detail: str
    service_code: str
    origin: str
    destination: str
    current_location: str
    weight_kg: float
    estimated_delivery: datetime | None
    original_estimated_delivery: datetime | None = None
    delivered_at: datetime | None = None
    exception: ShipmentException | None = None
    events: list[TrackingEvent] = Field(description="Newest first")


class Location(BaseModel):
    city: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    postal_code: str | None = None


class Dimensions(BaseModel):
    length_cm: float = Field(gt=0, le=300)
    width_cm: float = Field(gt=0, le=300)
    height_cm: float = Field(gt=0, le=300)


class RateRequest(BaseModel):
    origin: Location
    destination: Location
    weight_kg: float = Field(gt=0, le=1000)
    dimensions: Dimensions | None = None


class RateQuote(BaseModel):
    service_code: str
    service_name: str
    price: float
    currency: str = "USD"
    transit_days_min: int
    transit_days_max: int
    estimated_delivery: date
    chargeable_weight_kg: float
    includes: list[str] = Field(default_factory=list)


class RateResponse(BaseModel):
    origin: str
    destination: str
    distance_km: int
    actual_weight_kg: float
    volumetric_weight_kg: float | None
    quotes: list[RateQuote]


class Address(BaseModel):
    name: str | None = None
    company: str | None = None
    street1: str = ""
    street2: str | None = None
    city: str = ""
    state: str | None = None
    postal_code: str | None = None
    country: str = ""
    phone: str | None = None


class AddressValidationResponse(BaseModel):
    valid: bool
    issues: list[str]
    normalized: Address | None = None
    suggestions: list[Address] = Field(default_factory=list)


class Package(BaseModel):
    weight_kg: float = Field(gt=0, le=1000)
    dimensions: Dimensions | None = None
    description: str = Field(min_length=1)
    declared_value: float = Field(ge=0)
    currency: str = "USD"


class ShipmentRequest(BaseModel):
    shipper: Address
    recipient: Address
    package: Package
    service_code: str
    reference: str | None = None


class ShipmentResponse(BaseModel):
    tracking_number: str
    status: ShipmentStatus
    service_code: str
    price: float
    currency: str
    estimated_delivery: date
    label_url: str
    created_at: datetime
    reference: str | None = None


class ErrorResponse(BaseModel):
    error: str
    message: str

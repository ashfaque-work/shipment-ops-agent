"""Deterministic rate engine: price = f(distance, chargeable weight, service)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from mock_carrier.geo import City, haversine_km
from mock_carrier.models import Dimensions, RateQuote

VOLUMETRIC_DIVISOR = 5000  # cm^3 per kg, industry-standard for air express
FUEL_SURCHARGE = 0.115


@dataclass(frozen=True)
class Service:
    code: str
    name: str
    base: float
    per_kg: float
    transit_min: int
    transit_max: int
    min_chargeable_kg: float = 0.0
    includes: tuple[str, ...] = ()


SERVICES: dict[str, Service] = {
    s.code: s
    for s in [
        Service(
            "EXPRESS",
            "Mock Express Worldwide",
            38.0,
            13.5,
            2,
            4,
            includes=(
                "door-to-door",
                "customs brokerage",
                "real-time tracking",
                "money-back guarantee",
            ),
        ),
        Service(
            "STANDARD",
            "Mock Standard International",
            24.0,
            8.2,
            5,
            8,
            includes=("door-to-door", "customs brokerage", "tracking"),
        ),
        Service(
            "ECONOMY",
            "Mock Economy Select",
            16.0,
            5.1,
            9,
            14,
            includes=("door-to-door", "basic tracking"),
        ),
        Service(
            "FREIGHT",
            "Mock Air Freight (consolidated)",
            140.0,
            2.2,
            6,
            10,
            min_chargeable_kg=70.0,
            includes=("airport-to-door", "palletised", "customs brokerage"),
        ),
    ]
}


def volumetric_weight(dims: Dimensions | None) -> float | None:
    if dims is None:
        return None
    return round(dims.length_cm * dims.width_cm * dims.height_cm / VOLUMETRIC_DIVISOR, 2)


def quote(
    origin: City, destination: City, weight_kg: float, dims: Dimensions | None, now: datetime
) -> tuple[int, float | None, list[RateQuote]]:
    distance = haversine_km(origin, destination)
    vol = volumetric_weight(dims)
    chargeable = max(weight_kg, vol or 0.0)
    # Round chargeable weight up to the next 0.5 kg, like real carriers do.
    chargeable = round(-(-chargeable // 0.5) * 0.5, 1)
    distance_factor = 1 + distance / 6000
    long_haul = 1 if distance > 6000 else 0

    quotes: list[RateQuote] = []
    for svc in SERVICES.values():
        if chargeable < svc.min_chargeable_kg:
            continue
        linehaul = svc.base + svc.per_kg * chargeable * distance_factor
        price = round(linehaul * (1 + FUEL_SURCHARGE), 2)
        tmin, tmax = svc.transit_min + long_haul, svc.transit_max + long_haul
        quotes.append(
            RateQuote(
                service_code=svc.code,
                service_name=svc.name,
                price=price,
                transit_days_min=tmin,
                transit_days_max=tmax,
                estimated_delivery=(now + timedelta(days=tmax)).date(),
                chargeable_weight_kg=chargeable,
                includes=[*svc.includes, f"fuel surcharge {FUEL_SURCHARGE:.1%}"],
            )
        )
    quotes.sort(key=lambda q: q.price)
    return round(distance), vol, quotes

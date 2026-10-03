"""Static geography used by the mock carrier: supported cities, coordinates, postcode rules."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    name: str
    country: str  # ISO 3166-1 alpha-2
    lat: float
    lon: float
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    postcode_pattern: str | None  # None = country has no postcode system
    postcode_required: bool
    postcode_example: str | None = None


COUNTRIES: dict[str, Country] = {
    "IN": Country("IN", "India", r"^\d{6}$", True, "700001"),
    "AE": Country("AE", "United Arab Emirates", None, False),
    "KE": Country("KE", "Kenya", r"^\d{5}$", True, "00100"),
    "NG": Country("NG", "Nigeria", r"^\d{6}$", False, "100001"),
    "GH": Country("GH", "Ghana", r"^[A-Z]{2}-\d{3,4}-\d{4}$", False, "GA-123-4567"),
    "ZA": Country("ZA", "South Africa", r"^\d{4}$", True, "2196"),
}

CITIES: list[City] = [
    City("Kolkata", "IN", 22.5726, 88.3639, ("calcutta",)),
    City("Mumbai", "IN", 19.0760, 72.8777, ("bombay",)),
    City("Delhi", "IN", 28.7041, 77.1025, ("new delhi",)),
    City("Chennai", "IN", 13.0827, 80.2707, ("madras",)),
    City("Bengaluru", "IN", 12.9716, 77.5946, ("bangalore",)),
    City("Dubai", "AE", 25.2048, 55.2708),
    City("Abu Dhabi", "AE", 24.4539, 54.3773),
    City("Nairobi", "KE", -1.2921, 36.8219),
    City("Mombasa", "KE", -4.0435, 39.6682),
    City("Lagos", "NG", 6.5244, 3.3792),
    City("Abuja", "NG", 9.0765, 7.3986),
    City("Accra", "GH", 5.6037, -0.1870),
    City("Kumasi", "GH", 6.6885, -1.6244),
    City("Johannesburg", "ZA", -26.2041, 28.0473, ("joburg", "jhb")),
    City("Cape Town", "ZA", -33.9249, 18.4241),
    City("Durban", "ZA", -29.8587, 31.0218),
]

_CITY_INDEX: dict[tuple[str, str], City] = {}
for _c in CITIES:
    _CITY_INDEX[(_c.name.lower(), _c.country)] = _c
    for _alias in _c.aliases:
        _CITY_INDEX[(_alias, _c.country)] = _c


def find_city(name: str, country: str | None = None) -> City | None:
    """Exact (case-insensitive, alias-aware) city lookup. Country is optional."""
    key = name.strip().lower()
    if country:
        return _CITY_INDEX.get((key, country.strip().upper()))
    matches = {c for (n, _), c in _CITY_INDEX.items() if n == key}
    return next(iter(matches)) if len(matches) == 1 else None


def cities_in(country: str) -> list[City]:
    return [c for c in CITIES if c.country == country.upper()]


def haversine_km(a: City, b: City) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = p2 - p1
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def postcode_ok(country: str, postcode: str) -> bool:
    c = COUNTRIES[country]
    if c.postcode_pattern is None:
        return True
    return re.fullmatch(c.postcode_pattern, postcode.strip().upper()) is not None

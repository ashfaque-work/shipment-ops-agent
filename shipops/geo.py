"""Minimal city -> ISO country lookup so the agent can omit obvious country codes."""

from __future__ import annotations

CITY_COUNTRY: dict[str, str] = {
    "kolkata": "IN",
    "calcutta": "IN",
    "mumbai": "IN",
    "bombay": "IN",
    "delhi": "IN",
    "new delhi": "IN",
    "chennai": "IN",
    "bengaluru": "IN",
    "bangalore": "IN",
    "dubai": "AE",
    "abu dhabi": "AE",
    "nairobi": "KE",
    "mombasa": "KE",
    "lagos": "NG",
    "abuja": "NG",
    "accra": "GH",
    "kumasi": "GH",
    "johannesburg": "ZA",
    "joburg": "ZA",
    "cape town": "ZA",
    "durban": "ZA",
}

COUNTRY_NAMES: dict[str, str] = {
    "india": "IN",
    "uae": "AE",
    "united arab emirates": "AE",
    "kenya": "KE",
    "nigeria": "NG",
    "ghana": "GH",
    "south africa": "ZA",
}


def country_for(city: str, country: str | None = None) -> str | None:
    if country:
        c = country.strip()
        return COUNTRY_NAMES.get(c.lower(), c.upper())
    return CITY_COUNTRY.get(city.strip().lower())

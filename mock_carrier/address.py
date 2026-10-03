"""Rule-based address validation with fuzzy city suggestions."""

from __future__ import annotations

import difflib
import re

from mock_carrier.geo import COUNTRIES, cities_in, find_city, postcode_ok
from mock_carrier.models import Address, AddressValidationResponse

_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,18}$")


def validate(addr: Address) -> AddressValidationResponse:
    issues: list[str] = []
    suggestions: list[Address] = []
    country_code = addr.country.strip().upper()

    if country_code not in COUNTRIES:
        supported = ", ".join(sorted(COUNTRIES))
        return AddressValidationResponse(
            valid=False,
            issues=[f"Country '{addr.country}' is not served (supported: {supported})"],
        )
    country = COUNTRIES[country_code]

    city = find_city(addr.city, country_code) if addr.city.strip() else None
    if not addr.city.strip():
        issues.append("City is missing")
    elif city is None:
        names = [c.name for c in cities_in(country_code)]
        close = difflib.get_close_matches(addr.city.strip().title(), names, n=3, cutoff=0.6)
        if close:
            issues.append(f"City '{addr.city}' not recognised in {country.name}")
            suggestions.extend(addr.model_copy(update={"city": c}) for c in close)
        else:
            issues.append(f"City '{addr.city}' is not in the {country.name} service area")

    street = addr.street1.strip()
    if not street:
        issues.append("Street address is missing")
    elif not re.search(r"\d", street + (addr.street2 or "")):
        issues.append("No building/house number found in street address")

    postcode = (addr.postal_code or "").strip()
    if not postcode:
        if country.postcode_required:
            issues.append(
                f"Postal code is required for {country.name} (e.g. {country.postcode_example})"
            )
    elif not postcode_ok(country_code, postcode):
        issues.append(
            f"Postal code '{postcode}' has an invalid format for {country.name}"
            + (f" (e.g. {country.postcode_example})" if country.postcode_example else "")
        )

    if addr.phone and not _PHONE_RE.match(addr.phone.strip()):
        issues.append(f"Phone number '{addr.phone}' looks invalid")

    normalized = None
    if not issues and city is not None:
        normalized = addr.model_copy(
            update={
                "city": city.name,
                "country": country_code,
                "street1": " ".join(street.split()),
                "postal_code": postcode.upper() or None,
            }
        )
    return AddressValidationResponse(
        valid=not issues, issues=issues, normalized=normalized, suggestions=suggestions
    )

"""Field schema for shipping documents + value normalisation shared by extraction,
validation and evaluation."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

INVOICE = "commercial_invoice"
BOL = "bill_of_lading"
DOC_TYPES = (INVOICE, BOL)

# field -> kind. Kinds: id, name, text (fuzzy), date, num, int, phone, code
INVOICE_FIELDS: dict[str, str] = {
    "invoice_number": "id",
    "invoice_date": "date",
    "shipper_name": "name",
    "shipper_address": "text",
    "consignee_name": "name",
    "consignee_address": "text",
    "consignee_phone": "phone",
    "incoterm": "code",
    "currency": "code",
    "total_value": "num",
    "gross_weight_kg": "num",
    "net_weight_kg": "num",
    "num_packages": "int",
}
LINE_ITEM_FIELDS: dict[str, str] = {
    "description": "text",
    "hs_code": "id",
    "quantity": "num",
    "unit_price": "num",
    "amount": "num",
}
BOL_FIELDS: dict[str, str] = {
    "bol_number": "id",
    "issue_date": "date",
    "shipper_name": "name",
    "consignee_name": "name",
    "consignee_address": "text",
    "consignee_phone": "phone",
    "notify_party": "name",
    "vessel_voyage": "text",
    "port_of_loading": "text",
    "port_of_discharge": "text",
    "container_number": "id",
    "num_packages": "int",
    "gross_weight_kg": "num",
    "goods_description": "text",
    "invoice_reference": "id",
    "freight_terms": "code",
}

FIELDS = {INVOICE: INVOICE_FIELDS, BOL: BOL_FIELDS}

# Issue codes (shared by the generator's ground truth and the validator).
ISSUE_CODES = {
    "missing_hs_code": "A line item has no HS code",
    "invalid_hs_code": "An HS code is not in a valid format",
    "missing_consignee_phone": "Consignee phone number is missing",
    "missing_incoterm": "Incoterm is missing",
    "total_mismatch": "Invoice total does not equal the sum of line items",
    "missing_required_field": "A required field is missing",
    "weight_mismatch": "Gross weight differs between invoice and bill of lading",
    "package_count_mismatch": "Package count differs between invoice and bill of lading",
    "consignee_mismatch": "Consignee differs between invoice and bill of lading",
}


_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


def parse_date(value: Any) -> str | None:
    """Return ISO yyyy-mm-dd. Accepts ISO, '05 Sep 2026', 'Sep 5, 2026', dd/mm/yyyy."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    m = re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -,]*(\d{4})", s)
    if m and m[2].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])).isoformat()
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]* (\d{1,2}),? (\d{4})", s)
    if m and m[1].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS[m[1].lower()], int(m[2])).isoformat()
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)  # dd/mm/yyyy (non-US docs)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return None
    return None


def parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return float(value)
    s = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def normalize(kind: str, value: Any) -> Any:
    """Canonical form used for comparison. None means 'absent'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if kind == "date":
        return parse_date(value)
    if kind == "num":
        n = parse_number(value)
        return round(n, 2) if n is not None else None
    if kind == "int":
        n = parse_number(value)
        return int(round(n)) if n is not None else None
    if kind == "phone":
        digits = re.sub(r"\D", "", str(value))
        return digits or None
    if kind in ("id", "code"):
        return re.sub(r"[\s]+", "", str(value)).upper()
    return norm_text(value)


def text_similarity(a: str, b: str) -> float:
    """Token-set F1 between two normalised strings."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if not inter:
        return 0.0
    p, r = inter / len(ta), inter / len(tb)
    return 2 * p * r / (p + r)


def values_match(kind: str, predicted: Any, truth: Any) -> bool:
    p, t = normalize(kind, predicted), normalize(kind, truth)
    if p is None or t is None:
        return p is None and t is None
    if kind == "num":
        return abs(p - t) <= max(0.01, abs(t) * 0.005)
    if kind == "text":
        return text_similarity(p, t) >= 0.8
    if kind == "phone":
        return p[-9:] == t[-9:]
    return p == t

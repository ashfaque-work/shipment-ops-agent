"""Rule-based extractor over the PDF text layer (offline baseline).

Uses label synonyms ("Invoice No." / "Inv #" / "Invoice Number", "Consignee" /
"Buyer / Ship To" / "Consigned to", ...) rather than fixed positions. It cannot read
scanned documents (no text layer) - that is what the vision path is for.
"""

from __future__ import annotations

import re
from typing import Any

from shipops.documents.schema import BOL, INVOICE

_NUM = r"([\d,]+(?:\.\d+)?)"
_HS = re.compile(r"^\d{4}\.\d{2}(?:\.\d{2,4})?$")


def classify(text: str) -> str | None:
    up = text.upper()
    if "BILL OF LADING" in up or re.search(r"\bB/L NO\b|\bHBL\b", up):
        return BOL
    if "INVOICE" in up:
        return INVOICE
    return None


INVOICE_LABELS: dict[str, str] = {
    "invoice_number": r"(?:invoice\s*no\.?|inv\s*#|invoice\s*number)\s*[:#]?\s*(\S+)",
    "invoice_date": r"(?:invoice\s*date|date)\s*:?\s*(.+)",
    "incoterm": r"(?:incoterms?|terms\s*of\s*delivery|"
    r"delivery\s*terms(?:\s*\(incoterms\s*\d{4}\))?)\s*:?\s*([A-Z]{3})?\b.*",
    "currency": r"(?:invoice\s*)?currency\s*:?\s*([A-Z]{3})",
    "total_value": r"(?:grand\s*total(?:\s*\([A-Z]{3}\))?|total\s*invoice\s*value|total)\s*:?\s*"
    r"(?:[A-Z]{3}\s*)?" + _NUM,
    "gross_weight_kg": r"(?:total\s*)?gross\s*(?:weight|wt\.?)\s*(?:\(kg\))?\s*:?\s*" + _NUM,
    "net_weight_kg": r"(?:total\s*)?net\s*(?:weight|wt\.?)\s*(?:\(kg\))?\s*:?\s*" + _NUM,
    "num_packages": r"(?:no\.\s*of\s*packages|total\s*packages|number\s*of\s*packages|packages)"
    r"\s*:?\s*(\d+)",
}

BOL_LABELS: dict[str, str] = {
    "bol_number": r"(?:b/l\s*no\.?|hbl\s*number|bill\s*of\s*lading\s*no\.?)\s*:?\s*(\S+)",
    "issue_date": r"(?:place\s*and\s*date\s*of\s*issue|date\s*of\s*issue)\s*:?\s*(?:.*,\s*)?(.+)",
    "notify_party": r"(?:notify\s*party|notify)\s*:\s*(.+)",
    "vessel_voyage": r"(?:vessel\s*/\s*voyage|ocean\s*vessel|vessel)\s*:\s*(.+)",
    "port_of_loading": r"(?:port\s*of\s*loading|port\s*of\s*load)\s*:\s*(.+)",
    "port_of_discharge": r"(?:port\s*of\s*discharge|port\s*of\s*delivery)\s*:\s*(.+)",
    "container_number": r"container(?:\s*no\.?)?\s*:\s*([A-Z]{4}\s?\d{7})",
    "num_packages": r"(?:number\s*of\s*packages|packages)\s*:\s*(\d+)",
    "gross_weight_kg": r"gross\s*(?:weight|wt\.?)\s*:?\s*" + _NUM,
    "goods_description": r"(?:description\s*of\s*goods|goods)\s*:\s*(.+)",
    "freight_terms": r"freight(?:\s*payable)?\s*:\s*(prepaid|collect)",
    "invoice_reference": r"(?:invoice\s*ref|commercial\s*invoice\s*no)\s*:?\s*(\S+)",
}

PARTY_HEADERS = {
    INVOICE: {
        "shipper": r"(?:exporter|seller|shipper\s*/\s*exporter|shipper)\s*:?",
        "consignee": r"(?:consignee|buyer\s*/\s*ship\s*to|consigned\s*to)\s*:?",
    },
    BOL: {
        "shipper": r"(?:shipper|exporter)\s*:?",
        "consignee": r"(?:consignee|consigned\s*to)\s*:?",
    },
}
_PHONE = re.compile(r"^(?:tel|phone|contact|mobile)\s*[:.]?\s*(\+?[\d ()\-]{7,})$", re.I)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _is_label(line: str, labels: dict[str, str], headers: dict[str, str]) -> bool:
    for pat in [*labels.values(), *headers.values()]:
        if re.fullmatch(pat, line, re.I):
            return True
    return bool(re.match(r"^[A-Za-z][A-Za-z /.()#]{1,30}:\s", line)) or line.upper() in {
        "DESCRIPTION",
        "GOODS DESCRIPTION",
        "DESCRIPTION OF GOODS",
        "#",
        "ITEM",
        "NO",
    }


def _party(
    lines: list[str], header: str, labels: dict[str, str], headers: dict[str, str]
) -> dict[str, Any]:
    for i, ln in enumerate(lines):
        if re.fullmatch(header, ln, re.I):
            block = []
            for nxt in lines[i + 1 :]:
                if _is_label(nxt, labels, headers) and not _PHONE.match(nxt):
                    break
                block.append(nxt)
                if len(block) >= 6:
                    break
            if not block:
                return {}
            phone = next((m.group(1).strip() for b in block if (m := _PHONE.match(b))), None)
            rest = [b for b in block[1:] if not _PHONE.match(b)]
            return {"name": block[0], "address": ", ".join(rest) or None, "phone": phone}
    return {}


def _labelled(lines: list[str], labels: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, pat in labels.items():
        rx = re.compile(r"^" + pat + r"(?:\s.*)?$", re.I)  # allow trailing units ("kg")
        for ln in lines:
            m = rx.match(ln)
            if m:
                out[field] = m.group(1).strip() if m.group(1) else None
                break
    return out


def _line_items(lines: list[str]) -> list[dict[str, Any]]:
    start = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.lower() in ("description", "goods description", "description of goods")
        ),
        None,
    )
    if start is None:
        return []
    cells = []
    for ln in lines[start + 5 :]:  # skip the remaining header cells
        if re.match(r"^(grand\s*total|total)", ln, re.I):
            break
        cells.append(ln)
    items, i = [], 0
    while i < len(cells):
        if not re.fullmatch(r"\d{1,3}", cells[i]):
            i += 1
            continue
        row = cells[i + 1 : i + 6]
        if len(row) < 4:
            break
        desc = row[0]
        if _HS.match(row[1]):
            hs, nums = row[1], row[2:5]
            i += 6
        else:
            hs, nums = None, row[1:4]
            i += 5
        q, up, amt = (float(n.replace(",", "")) for n in nums)
        items.append(
            {"description": desc, "hs_code": hs, "quantity": q, "unit_price": up, "amount": amt}
        )
    return items


def extract(text: str, doc_type: str | None = None) -> dict[str, Any]:
    """Returns {"doc_type": ..., "fields": {...}}; missing values are None."""
    doc_type = doc_type or classify(text)
    lines = _lines(text)
    if doc_type == INVOICE:
        labels, headers = INVOICE_LABELS, PARTY_HEADERS[INVOICE]
    elif doc_type == BOL:
        labels, headers = BOL_LABELS, PARTY_HEADERS[BOL]
    else:
        return {"doc_type": None, "fields": {}}

    fields = _labelled(lines, labels)
    shipper = _party(lines, headers["shipper"], labels, headers)
    consignee = _party(lines, headers["consignee"], labels, headers)
    fields["shipper_name"] = shipper.get("name")
    fields["consignee_name"] = consignee.get("name")
    fields["consignee_address"] = consignee.get("address")
    fields["consignee_phone"] = consignee.get("phone")

    if doc_type == INVOICE:
        fields["shipper_address"] = shipper.get("address")
        fields["line_items"] = _line_items(lines)
        if not fields.get("currency"):
            m = re.search(r"\b(USD|EUR|INR|AED|GBP)\b", text)
            fields["currency"] = m.group(1) if m else None
    else:
        # goods description may wrap onto a continuation line
        if fields.get("goods_description"):
            idx = next(i for i, ln in enumerate(lines) if fields["goods_description"] in ln)
            if idx + 1 < len(lines) and not _is_label(lines[idx + 1], labels, headers):
                fields["goods_description"] += lines[idx + 1]
    return {"doc_type": doc_type, "fields": fields}

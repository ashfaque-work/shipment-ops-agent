"""Deterministic document checks. Problems are flagged by rules over extracted fields,
never by asking the model "is anything wrong?" - so detection is auditable and testable."""

from __future__ import annotations

import re
from typing import Any

from shipops.documents.schema import BOL, INVOICE, norm_text, text_similarity

HS_FORMAT = re.compile(r"^\d{4}(\.?\d{2}){1,3}$")
WEIGHT_TOLERANCE = 0.02  # 2 %

REQUIRED = {
    INVOICE: [
        "invoice_number",
        "invoice_date",
        "shipper_name",
        "consignee_name",
        "consignee_address",
        "currency",
        "total_value",
        "gross_weight_kg",
    ],
    BOL: [
        "bol_number",
        "shipper_name",
        "consignee_name",
        "port_of_loading",
        "port_of_discharge",
        "gross_weight_kg",
        "num_packages",
    ],
}


def _issue(code: str, field: str, message: str, severity: str = "error", **extra) -> dict:
    return {"code": code, "field": field, "message": message, "severity": severity} | extra


def check_document(doc_type: str | None, f: dict[str, Any]) -> list[dict]:
    if doc_type not in (INVOICE, BOL):
        return [
            _issue(
                "unknown_document_type",
                "doc_type",
                "Could not tell whether this is an invoice or a bill of lading",
            )
        ]
    issues = [
        _issue("missing_required_field", k, f"Required field '{k}' is missing")
        for k in REQUIRED[doc_type]
        if f.get(k) in (None, "")
    ]
    if not f.get("consignee_phone"):
        issues.append(
            _issue(
                "missing_consignee_phone",
                "consignee_phone",
                "Consignee phone number is missing - carriers and customs often "
                "need it to arrange delivery",
            )
        )
    if doc_type == INVOICE:
        if not f.get("incoterm"):
            issues.append(
                _issue(
                    "missing_incoterm",
                    "incoterm",
                    "Incoterm is missing - who pays freight/duty is undefined",
                    "warning",
                )
            )
        items = f.get("line_items") or []
        if not items:
            issues.append(_issue("missing_required_field", "line_items", "No line items found"))
        for i, it in enumerate(items):
            hs = (it.get("hs_code") or "").strip()
            label = it.get("description") or f"line {i + 1}"
            if not hs:
                issues.append(
                    _issue(
                        "missing_hs_code",
                        f"line_items[{i}].hs_code",
                        f"Line {i + 1} ({label}) has no HS code - customs will hold the shipment",
                    )
                )
            elif not HS_FORMAT.match(hs):
                issues.append(
                    _issue(
                        "invalid_hs_code",
                        f"line_items[{i}].hs_code",
                        f"Line {i + 1} HS code '{hs}' is not a valid format",
                    )
                )
        amounts = [it.get("amount") for it in items]
        total = f.get("total_value")
        if items and total is not None and all(a is not None for a in amounts):
            s = round(sum(amounts), 2)
            if abs(s - total) > max(1.0, 0.005 * total):
                issues.append(
                    _issue(
                        "total_mismatch",
                        "total_value",
                        f"Invoice total {total:,.2f} does not match the sum of "
                        f"line items {s:,.2f} (difference {total - s:+,.2f})",
                    )
                )
    return issues


def pair_documents(docs: list[dict]) -> list[tuple[dict, dict]]:
    """Match invoices to BoLs via the BoL's invoice reference (or 1:1 if unambiguous)."""
    invoices = [d for d in docs if d["doc_type"] == INVOICE]
    bols = [d for d in docs if d["doc_type"] == BOL]
    pairs, used = [], set()
    for b in bols:
        ref = norm_text(b["fields"].get("invoice_reference") or "")
        match = next(
            (
                i
                for i in invoices
                if ref and norm_text(i["fields"].get("invoice_number") or "") == ref
            ),
            None,
        )
        if match is None and len(invoices) == 1 and len(bols) == 1:
            match = invoices[0]
        if match is not None and match["document_id"] not in used:
            used.add(match["document_id"])
            pairs.append((match, b))
    return pairs


def check_pair(inv: dict, bol: dict) -> list[dict]:
    fi, fb = inv["fields"], bol["fields"]
    ids = [inv["document_id"], bol["document_id"]]
    issues = []
    gi, gb = fi.get("gross_weight_kg"), fb.get("gross_weight_kg")
    if gi and gb and abs(gi - gb) / max(gi, gb) > WEIGHT_TOLERANCE:
        issues.append(
            _issue(
                "weight_mismatch",
                "gross_weight_kg",
                f"Gross weight differs: invoice {gi:,.2f} kg vs bill of lading "
                f"{gb:,.2f} kg ({(gb - gi) / gi:+.0%}). Customs may hold or "
                "re-weigh the cargo.",
                document_ids=ids,
            )
        )
    pi, pb = fi.get("num_packages"), fb.get("num_packages")
    if pi and pb and int(pi) != int(pb):
        issues.append(
            _issue(
                "package_count_mismatch",
                "num_packages",
                f"Package count differs: invoice {pi} vs bill of lading {pb}",
                document_ids=ids,
            )
        )
    ci, cb = fi.get("consignee_name"), fb.get("consignee_name")
    if ci and cb and text_similarity(norm_text(ci), norm_text(cb)) < 0.8:
        issues.append(
            _issue(
                "consignee_mismatch",
                "consignee_name",
                f"Consignee differs: '{ci}' vs '{cb}'",
                document_ids=ids,
            )
        )
    return issues

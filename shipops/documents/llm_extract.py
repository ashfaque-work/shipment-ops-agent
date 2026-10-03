"""LLM extraction: Gemini Flash reads the text layer, or page images for scanned PDFs.

The model is asked to transcribe, not to infer: fields that are not printed must be
null. Problem detection is NOT delegated to the model - see validate.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

from shipops.documents.schema import BOL_FIELDS, INVOICE_FIELDS, LINE_ITEM_FIELDS
from shipops.llm.base import LLM, LLMError

_KIND_HINT = {
    "date": "YYYY-MM-DD",
    "num": "number",
    "int": "integer",
    "phone": "string",
    "id": "string",
    "code": "string",
    "name": "string",
    "text": "string",
}


def _schema_lines(fields: dict[str, str]) -> str:
    return "\n".join(f"  - {k}: {_KIND_HINT[v]}" for k, v in fields.items())


EXTRACTION_PROMPT = f"""You extract data from a shipping document.
First decide doc_type: "commercial_invoice" or "bill_of_lading".
Return ONLY a JSON object: {{"doc_type": ..., "fields": {{...}}}}.

commercial_invoice fields:
{_schema_lines(INVOICE_FIELDS)}
  - line_items: list of objects with
{_schema_lines(LINE_ITEM_FIELDS)}

bill_of_lading fields:
{_schema_lines(BOL_FIELDS)}

Rules:
- Transcribe exactly what is printed. If a field is not present or blank, use null.
  NEVER guess or fill in a missing value (e.g. a missing HS code or phone must be null).
- Dates as YYYY-MM-DD. Dates written like 05/09/2026 are day/month/year.
- Numbers without thousands separators or currency symbols. Weights in kg.
- Addresses: one line, parts separated by ", ", excluding the company name and phone.
- shipper_address only for invoices. consignee_phone: the consignee's phone/tel/contact.
"""


def _parse_json(content: str | None) -> dict[str, Any]:
    if not content:
        raise LLMError("empty extraction response")
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise LLMError("extraction response is not JSON") from None
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise LLMError("extraction response is not an object")
    if "fields" not in data:  # tolerate a flat object
        data = {"doc_type": data.pop("doc_type", None), "fields": data}
    return data


def build_messages(text: str | None, images_b64: list[str] | None) -> list[dict[str, Any]]:
    if images_b64:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "Extract the fields from this scanned page."}
        ]
        content += [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
            for b in images_b64
        ]
    else:
        content = f"Document text (from the PDF text layer):\n\n{text}"
    return [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": content}]


def extract(
    llm: LLM, *, text: str | None = None, images_b64: list[str] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns ({"doc_type", "fields"}, meta)."""
    res = llm.complete(build_messages(text, images_b64), [])
    data = _parse_json(res.message.get("content"))
    meta = {
        "provider": res.provider,
        "model": res.model,
        "latency_ms": res.latency_ms,
        "fallback_from": res.fallback_from,
    }
    return data, meta

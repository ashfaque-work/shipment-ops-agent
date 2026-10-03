"""Extractor (rules or LLM) + in-memory DocumentStore used by the API and agent tool."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from shipops.documents import llm_extract, pdf, rules
from shipops.documents.schema import (
    DOC_TYPES,
    FIELDS,
    INVOICE,
    LINE_ITEM_FIELDS,
    parse_date,
    parse_number,
)
from shipops.documents.validate import check_document, check_pair, pair_documents
from shipops.llm.base import LLM, LLMError

log = logging.getLogger(__name__)


def _coerce(kind: str, value: Any) -> Any:
    if value in (None, "", "null", "N/A", "n/a"):
        return None
    if kind == "date":
        return parse_date(value) or str(value)
    if kind == "num":
        return parse_number(value)
    if kind == "int":
        n = parse_number(value)
        return int(round(n)) if n is not None else None
    return str(value).strip() or None


INCOTERMS_2020 = re.compile(r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b")


def normalize_incoterm(value: str | None) -> str | None:
    """'DAP 2020', 'CIF (Incoterms 2020)', 'fob' -> the bare 3-letter code."""
    if not value:
        return None
    m = INCOTERMS_2020.search(value.upper())
    return m.group(1) if m else value.strip()


def clean_fields(doc_type: str | None, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce extractor output onto the schema (types, nulls, known keys only)."""
    if doc_type not in FIELDS:
        return {}
    out = {k: _coerce(kind, raw.get(k)) for k, kind in FIELDS[doc_type].items()}
    if doc_type == INVOICE:
        out["incoterm"] = normalize_incoterm(out.get("incoterm"))
        out["line_items"] = [
            {k: _coerce(kind, (it or {}).get(k)) for k, kind in LINE_ITEM_FIELDS.items()}
            for it in raw.get("line_items") or []
            if isinstance(it, dict)
        ]
    return out


@dataclass
class Extraction:
    doc_type: str | None
    fields: dict[str, Any]
    method: str  # rules | llm_text | llm_vision | none
    scanned: bool
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class Extractor:
    """mode: 'rules' (offline baseline), 'llm', or 'auto' (llm when configured)."""

    def __init__(
        self, mode: str = "auto", text_llm: LLM | None = None, vision_llm: LLM | None = None
    ):
        self.mode = mode
        self.text_llm, self.vision_llm = text_llm, vision_llm

    def extract(self, data: bytes) -> Extraction:
        text = pdf.extract_text(data)
        scanned = pdf.is_scanned(text)
        use_llm = self.mode == "llm" or (self.mode == "auto" and self.text_llm is not None)
        try:
            if scanned:
                if self.mode != "rules" and self.vision_llm is not None:
                    raw, meta = llm_extract.extract(
                        self.vision_llm, images_b64=pdf.page_images_b64(data)
                    )
                    return self._result(raw, "llm_vision", True, meta)
                return Extraction(
                    None,
                    {},
                    "none",
                    True,
                    [
                        "Scanned document (no text layer): vision extraction needs a Gemini API "
                        "key (GEMINI_API_KEY)."
                    ],
                )
            if use_llm and self.text_llm is not None:
                raw, meta = llm_extract.extract(self.text_llm, text=text)
                result = self._result(raw, "llm_text", False, meta)
                if result.doc_type is None:  # fall back to keyword classification
                    result.doc_type = rules.classify(text)
                    result.fields = clean_fields(result.doc_type, raw.get("fields") or {})
                return result
        except (LLMError, ValueError) as exc:
            log.warning("LLM extraction failed, falling back to rules: %s", exc)
            res = self._result(rules.extract(text), "rules", scanned, {})
            res.warnings.append(f"LLM extraction unavailable ({exc}); used rule-based parser.")
            return res
        return self._result(rules.extract(text), "rules", False, {})

    @staticmethod
    def _result(raw: dict, method: str, scanned: bool, meta: dict) -> Extraction:
        doc_type = raw.get("doc_type")
        doc_type = doc_type if doc_type in DOC_TYPES else None
        return Extraction(
            doc_type, clean_fields(doc_type, raw.get("fields") or {}), method, scanned, meta=meta
        )


def document_id_for(data: bytes) -> str:
    """Content-addressed id: identical uploads share an id (and an extraction cache)."""
    return "doc_" + hashlib.sha256(data).hexdigest()[:10]


class DocumentStore:
    def __init__(self, extractor: Extractor, *, max_docs: int = 500):
        self.extractor = extractor
        self.max_docs = max_docs
        self._docs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, filename: str, data: bytes) -> dict[str, Any]:
        doc_id = document_id_for(data)
        with self._lock:
            if doc_id in self._docs:
                return self._docs[doc_id]
        ex = self.extractor.extract(data)
        record = {
            "document_id": doc_id,
            "filename": filename,
            "doc_type": ex.doc_type,
            "method": ex.method,
            "scanned": ex.scanned,
            "fields": ex.fields,
            "issues": check_document(ex.doc_type, ex.fields) if ex.doc_type else [],
            "warnings": ex.warnings,
            "meta": ex.meta,
        }
        with self._lock:
            if len(self._docs) >= self.max_docs:  # simple FIFO eviction
                self._docs.pop(next(iter(self._docs)))
            self._docs[doc_id] = record
        return record

    def get(self, doc_id: str) -> dict[str, Any] | None:
        return self._docs.get(doc_id)

    def analyze(self, ids: list[str]) -> dict[str, Any]:
        found = [self._docs[i] for i in dict.fromkeys(ids) if i in self._docs]
        missing = [i for i in ids if i not in self._docs]
        if not found:
            return {
                "ok": False,
                "error": "documents_not_found",
                "message": f"No uploaded documents with ids: {', '.join(ids) or '(none)'}",
                "guidance": "Ask the user to upload the documents first.",
            }
        cross: list[dict] = []
        for inv, bol in pair_documents(found):
            cross += check_pair(inv, bol)
        return {
            "ok": True,
            "documents": [
                {
                    k: d[k]
                    for k in (
                        "document_id",
                        "filename",
                        "doc_type",
                        "method",
                        "fields",
                        "issues",
                        "warnings",
                    )
                }
                for d in found
            ],
            "cross_document_issues": cross,
            **({"missing_ids": missing} if missing else {}),
        }

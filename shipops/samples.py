"""Preloaded sample documents for one-click "try this" buttons in the UI."""

from __future__ import annotations

from pathlib import Path

PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "documents" / "pdf"

SAMPLES: list[dict] = [
    {
        "id": "weight-mismatch",
        "title": "Invoice + bill of lading, weights disagree",
        "files": ["p03_invoice", "p03_bol"],
        "prompt": "Please check these documents before we send them to the forwarder.",
    },
    {
        "id": "missing-hs-code",
        "title": "Invoice with a missing HS code",
        "files": ["p02_invoice"],
        "prompt": "Can you review this invoice?",
    },
    {
        "id": "clean-packet",
        "title": "Complete invoice + bill of lading",
        "files": ["p01_invoice", "p01_bol"],
        "prompt": "Do these documents match?",
    },
    {
        "id": "scanned-invoice",
        "title": "Scanned invoice (needs vision)",
        "files": ["p06_invoice"],
        "prompt": "What's in this scanned invoice? Anything missing?",
    },
]


def get_sample(sample_id: str) -> dict | None:
    return next((s for s in SAMPLES if s["id"] == sample_id), None)


def sample_files(sample: dict) -> list[tuple[str, bytes]]:
    return [(f"{name}.pdf", (PDF_DIR / f"{name}.pdf").read_bytes()) for name in sample["files"]]

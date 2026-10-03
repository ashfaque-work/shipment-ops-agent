"""PDF helpers: text-layer extraction, scan detection, page rendering for vision."""

from __future__ import annotations

import base64
import io

MIN_TEXT_CHARS = 40  # below this a PDF is treated as a scan (image-only)


class PdfError(ValueError):
    pass


def extract_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:5])
    except (PdfReadError, ValueError, KeyError) as exc:
        raise PdfError(f"Could not read PDF: {exc}") from exc


def is_scanned(text: str) -> bool:
    return len(text.strip()) < MIN_TEXT_CHARS


def page_images_b64(data: bytes, *, max_pages: int = 2, scale: float = 1.4) -> list[str]:
    """Render pages to base64 PNGs (for vision models)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(min(len(pdf), max_pages)):
            img = pdf[i].render(scale=scale).to_pil().convert("L")
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            out.append(base64.b64encode(buf.getvalue()).decode())
        return out
    finally:
        pdf.close()

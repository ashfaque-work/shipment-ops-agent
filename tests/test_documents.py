from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shipops.agent import tools as T
from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.api.main import create_app
from shipops.config import Settings
from shipops.documents.schema import BOL, INVOICE, normalize, parse_date, values_match
from shipops.documents.store import DocumentStore, Extractor, document_id_for
from shipops.documents.validate import check_document
from shipops.llm.baseline import RuleBasedLLM
from shipops.llm.scripted import ScriptedLLM

DOCS = Path(__file__).resolve().parents[1] / "data" / "documents"


def pdf_bytes(doc_id: str) -> bytes:
    return (DOCS / "pdf" / f"{doc_id}.pdf").read_bytes()


def gt(doc_id: str) -> dict:
    return json.loads((DOCS / "ground_truth" / f"{doc_id}.json").read_text())


# ---------- schema / normalisation ----------


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("2026-08-09", "2026-08-09"),
        ("05 Sep 2026", "2026-09-05"),
        ("18/08/2026", "2026-08-18"),
        ("Sep 5, 2026", "2026-09-05"),
        ("garbage", None),
    ],
)
def test_parse_date(raw: str, iso: str | None) -> None:
    assert parse_date(raw) == iso


def test_values_match_kinds() -> None:
    assert values_match("num", "1,135.80", 1135.8)
    assert values_match("phone", "+254 711 555 090", "254711555090")
    assert values_match(
        "text",
        "12 Ngong Road, Kilimani, Nairobi 00100, Kenya",
        "12 Ngong Road Kilimani Nairobi 00100 Kenya",
    )
    assert not values_match("id", "MST/2026/0114", "MST/2026/0115")
    assert values_match("id", None, None) and not values_match("id", "x", None)
    assert normalize("code", " fob ") == "FOB"


# ---------- corpus ----------


def test_corpus_shape() -> None:
    manifest = json.loads((DOCS / "manifest.json").read_text())
    docs = manifest["documents"]
    assert len(docs) == 25
    assert sum(d["scanned"] for d in docs) == 5
    codes = [c for d in docs for c in d["issues"]]
    for code in ("missing_hs_code", "weight_mismatch", "missing_consignee_phone"):
        assert code in codes
    for d in docs:
        assert (DOCS / "pdf" / f"{d['document_id']}.pdf").exists()


def test_generator_is_deterministic(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_documents.py"
    subprocess.run(
        [sys.executable, str(script), "--out", str(tmp_path / "d")], check=True, capture_output=True
    )
    for p in (DOCS / "ground_truth").glob("*.json"):
        assert json.loads(p.read_text()) == json.loads(
            (tmp_path / "d" / "ground_truth" / p.name).read_text()
        )


# ---------- validation ----------


def _invoice(**over) -> dict:
    f = {
        "invoice_number": "X/1",
        "invoice_date": "2026-08-01",
        "shipper_name": "A",
        "consignee_name": "B",
        "consignee_address": "C",
        "consignee_phone": "+1 555 0100",
        "currency": "USD",
        "incoterm": "FOB",
        "total_value": 30.0,
        "gross_weight_kg": 5.0,
        "line_items": [
            {
                "description": "a",
                "hs_code": "6304.19",
                "quantity": 1,
                "unit_price": 10.0,
                "amount": 10.0,
            },
            {
                "description": "b",
                "hs_code": "4602.12",
                "quantity": 2,
                "unit_price": 10.0,
                "amount": 20.0,
            },
        ],
    }
    return f | over


def codes(issues: list[dict]) -> set[str]:
    return {i["code"] for i in issues}


def test_clean_invoice_has_no_issues() -> None:
    assert check_document(INVOICE, _invoice()) == []


def test_invoice_problems_detected() -> None:
    f = _invoice(consignee_phone=None, incoterm=None, total_value=99.0)
    f["line_items"][1]["hs_code"] = None
    f["line_items"][0]["hs_code"] = "63A4"
    assert codes(check_document(INVOICE, f)) == {
        "missing_consignee_phone",
        "missing_incoterm",
        "total_mismatch",
        "missing_hs_code",
        "invalid_hs_code",
    }


def test_unknown_type() -> None:
    assert codes(check_document(None, {})) == {"unknown_document_type"}


# ---------- extraction ----------


@pytest.mark.parametrize(
    "doc_id", ["p01_invoice", "p02_invoice", "p03_invoice", "p01_bol", "p02_bol"]
)
def test_rules_extract_text_documents(doc_id: str) -> None:
    truth = gt(doc_id)
    ex = Extractor("rules").extract(pdf_bytes(doc_id))
    assert ex.method == "rules" and ex.doc_type == truth["doc_type"]
    from shipops.documents.schema import FIELDS

    for k, kind in FIELDS[truth["doc_type"]].items():
        assert values_match(kind, ex.fields.get(k), truth["fields"].get(k)), k


def test_rules_cannot_read_scans() -> None:
    ex = Extractor("rules").extract(pdf_bytes("p05_invoice"))
    assert ex.scanned and ex.method == "none" and ex.doc_type is None
    assert "GEMINI_API_KEY" in ex.warnings[0]


def test_llm_text_extraction_parses_json() -> None:
    truth = gt("p02_invoice")
    reply = {
        "role": "assistant",
        "content": "```json\n"
        + json.dumps({"doc_type": INVOICE, "fields": truth["fields"]})
        + "\n```",
    }
    llm = ScriptedLLM([reply])
    ex = Extractor("auto", text_llm=llm).extract(pdf_bytes("p02_invoice"))
    assert ex.method == "llm_text" and ex.fields["line_items"][0]["hs_code"] is None
    sent = llm.requests[0][1]["content"]
    assert "MST/2026/0114" in sent  # the text layer was sent, not an image


def test_llm_vision_used_for_scans() -> None:
    truth = gt("p06_invoice")
    llm = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": json.dumps({"doc_type": INVOICE, "fields": truth["fields"]}),
            }
        ]
    )
    ex = Extractor("auto", text_llm=ScriptedLLM([]), vision_llm=llm).extract(
        pdf_bytes("p06_invoice")
    )
    assert ex.method == "llm_vision" and ex.scanned
    parts = llm.requests[0][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_llm_failure_falls_back_to_rules() -> None:
    ex = Extractor(
        "auto", text_llm=ScriptedLLM([{"role": "assistant", "content": "no json"}])
    ).extract(pdf_bytes("p01_invoice"))
    assert ex.method == "rules" and ex.fields["invoice_number"] == "BCE/2026/0107"
    assert ex.warnings


# ---------- store / cross-document ----------


@pytest.fixture()
def store() -> DocumentStore:
    return DocumentStore(Extractor("rules"))


def test_weight_mismatch_across_packet(store: DocumentStore) -> None:
    a = store.add("inv.pdf", pdf_bytes("p03_invoice"))
    b = store.add("bol.pdf", pdf_bytes("p03_bol"))
    res = store.analyze([a["document_id"], b["document_id"]])
    assert res["ok"]
    assert codes(res["cross_document_issues"]) == {"weight_mismatch"}


def test_clean_packet_no_cross_issues(store: DocumentStore) -> None:
    ids = [store.add(f, pdf_bytes(f))["document_id"] for f in ("p01_invoice", "p01_bol")]
    res = store.analyze(ids)
    assert res["cross_document_issues"] == []
    assert all(d["issues"] == [] for d in res["documents"])


def test_content_addressed_ids(store: DocumentStore) -> None:
    data = pdf_bytes("p01_invoice")
    assert store.add("a.pdf", data)["document_id"] == document_id_for(data)
    assert store.add("b.pdf", data)["filename"] == "a.pdf"  # cached


def test_analyze_unknown_ids(store: DocumentStore) -> None:
    assert store.analyze(["doc_nope"])["error"] == "documents_not_found"


def test_agent_tool_analyze_documents(ctx: ToolContext, store: DocumentStore) -> None:
    ctx.documents = store
    doc_id = store.add("p02.pdf", pdf_bytes("p02_invoice"))["document_id"]
    out = T.execute("analyze_documents", {"document_ids": [doc_id]}, ctx)
    assert codes(out["documents"][0]["issues"]) == {"missing_hs_code"}
    # baseline agent end-to-end
    reply = AgentService(RuleBasedLLM(), ctx).chat("s", f"check {doc_id} please").reply
    assert "no HS code" in reply


# ---------- API ----------


def test_upload_endpoint(ctx: ToolContext, store: DocumentStore) -> None:
    ctx.documents = store
    app = create_app(
        Settings(llm_provider="offline", max_upload_mb=1), service=AgentService(RuleBasedLLM(), ctx)
    )
    api = TestClient(app)
    r = api.post(
        "/api/documents", files={"file": ("p04_bol.pdf", pdf_bytes("p04_bol"), "application/pdf")}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == BOL and codes(body["issues"]) == {"missing_consignee_phone"}
    assert (
        api.post("/api/documents", files={"file": ("x.txt", b"hello", "text/plain")}).status_code
        == 415
    )
    big = b"%PDF" + b"0" * (1024 * 1024 + 10)
    assert (
        api.post("/api/documents", files={"file": ("big.pdf", big, "application/pdf")}).status_code
        == 413
    )


def test_sample_endpoints(ctx: ToolContext, store: DocumentStore) -> None:
    ctx.documents = store
    api = TestClient(
        create_app(Settings(llm_provider="offline"), service=AgentService(RuleBasedLLM(), ctx))
    )
    samples = api.get("/api/samples").json()
    assert {s["id"] for s in samples} >= {"weight-mismatch", "missing-hs-code"}
    r = api.post("/api/samples/weight-mismatch").json()
    assert len(r["documents"]) == 2 and r["prompt"]
    assert api.post("/api/samples/nope").status_code == 404


@pytest.mark.parametrize(
    "raw,code",
    [
        ("DAP 2020", "DAP"),
        ("CIF (Incoterms 2020)", "CIF"),
        ("fob", "FOB"),
        (None, None),
        ("Ex works", "Ex works"),
    ],
)
def test_incoterm_normalised(raw: str | None, code: str | None) -> None:
    from shipops.documents.store import normalize_incoterm

    assert normalize_incoterm(raw) == code

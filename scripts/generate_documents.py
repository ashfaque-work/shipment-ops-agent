"""Generate synthetic shipping documents (PDF) with ground-truth JSON.

    uv run python scripts/generate_documents.py [--out data/documents] [--seed 7]

Produces 12 shipment "packets" (commercial invoice + bill of lading) and one standalone
invoice = 25 PDFs, using 3 invoice and 2 BoL layouts. Five documents are rasterised to
image-only "scans" (no text layer) so extraction must use vision. Some documents carry
deliberate problems, recorded in the ground truth:

    missing_hs_code, missing_consignee_phone, missing_incoterm, total_mismatch (per doc)
    weight_mismatch (per packet: invoice vs BoL gross weight)

ALL DATA IS FAKE: companies, people, addresses, phone numbers (555 ranges) and vessels.
Output is deterministic for a given seed.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shipops.documents.schema import BOL, INVOICE  # noqa: E402

W, H = A4

EXPORTERS = [
    (
        "Bengal Craft Exports Pvt Ltd",
        ["22 Park Street", "Kolkata 700016, West Bengal, India"],
        "INCCU",
        "Kolkata Port, India",
    ),
    (
        "Malabar Spice Traders LLP",
        ["41 Bazaar Road, Mattancherry", "Kochi 682002, Kerala, India"],
        "INCOK",
        "Cochin Port, India",
    ),
    (
        "Rajputana Textile House",
        ["8 Johari Bazaar", "Jaipur 302003, Rajasthan, India"],
        "INNSA",
        "Nhava Sheva, India",
    ),
    (
        "Gulf Horizon General Trading LLC",
        ["Office 1204, Al Fahidi Tower", "Bur Dubai, Dubai, UAE"],
        "AEJEA",
        "Jebel Ali, UAE",
    ),
    (
        "Deccan Leatherworks Pvt Ltd",
        ["17 Anna Salai", "Chennai 600002, Tamil Nadu, India"],
        "INMAA",
        "Chennai Port, India",
    ),
]

IMPORTERS = [
    (
        "Lekki Home Stores Ltd",
        ["Plot 9, Admiralty Way, Lekki Phase 1", "Lagos 106104, Nigeria"],
        "+234 803 555 0142",
        "NGAPP",
        "Apapa, Lagos, Nigeria",
    ),
    (
        "Savannah Interiors Ltd",
        ["7 Riverside Drive, Westlands", "Nairobi 00100, Kenya"],
        "+254 712 555 018",
        "KEMBA",
        "Mombasa, Kenya",
    ),
    (
        "Osu Market Supplies",
        ["21 Oxford Street, Osu", "Accra GA-123-4567, Ghana"],
        "+233 24 555 0190",
        "GHTEM",
        "Tema, Ghana",
    ),
    (
        "Rosebank Living (Pty) Ltd",
        ["33 Jan Smuts Avenue, Rosebank", "Johannesburg 2196, South Africa"],
        "+27 82 555 0177",
        "ZADUR",
        "Durban, South Africa",
    ),
    (
        "Kilimani Decor Co.",
        ["12 Ngong Road, Kilimani", "Nairobi 00100, Kenya"],
        "+254 711 555 090",
        "KEMBA",
        "Mombasa, Kenya",
    ),
    (
        "Ikeja Trade Hub Ltd",
        ["14 Opebi Road, Ikeja", "Lagos 100271, Nigeria"],
        "+234 802 555 0177",
        "NGAPP",
        "Apapa, Lagos, Nigeria",
    ),
]

GOODS = [
    ("Cotton kantha quilts", "6304.19", 18.0, 1.6),
    ("Jute floor rugs 120x180cm", "5702.39", 24.0, 3.8),
    ("Brass table lamps", "9405.21", 32.0, 2.1),
    ("Terracotta planters", "6913.90", 9.5, 1.9),
    ("Silk stoles", "6214.10", 21.0, 0.2),
    ("Black pepper, whole (25 kg bags)", "0904.11", 120.0, 25.5),
    ("Green cardamom (10 kg cartons)", "0908.31", 310.0, 10.4),
    ("Leather handbags", "4202.21", 46.0, 0.9),
    ("Handloom cotton sarees", "5208.52", 27.0, 0.6),
    ("Cane storage baskets", "4602.12", 7.5, 0.7),
    ("Wooden toys, hand-painted", "9503.00", 6.0, 0.4),
    ("Turmeric powder (1 kg packs)", "0910.30", 3.2, 1.05),
]

VESSELS = [
    "MV Coral Meridian",
    "MV Indus Star",
    "MV Kalinga Trader",
    "MV Tamarind Bay",
    "MV Sahel Voyager",
    "MV Monsoon Pearl",
]
INCOTERMS = ["FOB", "CIF", "CFR", "DAP", "EXW"]
CONTAINER_PREFIX = ["TGHU", "MSMU", "CAIU", "TCNU", "SEGU"]

# packet number -> deliberate problems (see module docstring)
PROBLEMS: dict[int, set[str]] = {
    2: {"inv:missing_hs_code"},
    3: {"weight_mismatch"},
    4: {"bol:missing_consignee_phone"},
    6: {"inv:missing_hs_code"},
    7: {"weight_mismatch", "inv:missing_consignee_phone"},
    8: {"inv:total_mismatch"},
    10: {"inv:missing_incoterm"},
    11: {"weight_mismatch"},
    12: {"inv:missing_hs_code", "inv:total_mismatch"},
    13: {"inv:missing_consignee_phone"},
}
SCANNED = {"p05_invoice", "p06_invoice", "p09_bol", "p11_bol", "p12_invoice"}
STANDALONE = {13}  # invoice only, no BoL
N_PACKETS = 13


@dataclass
class Doc:
    doc_id: str
    doc_type: str
    packet_id: str
    layout: str
    fields: dict
    issues: list[dict] = field(default_factory=list)
    scanned: bool = False


# ------------------------------------------------------------------------------------
# Data synthesis
# ------------------------------------------------------------------------------------


def make_packet(n: int, rng: random.Random) -> list[Doc]:
    problems = PROBLEMS.get(n, set())
    exp = EXPORTERS[(n - 1) % len(EXPORTERS)]
    imp = IMPORTERS[(n * 2) % len(IMPORTERS)]
    inv_date = date(2026, 8, 3) + timedelta(days=4 * n + rng.randint(0, 3))
    bol_date = inv_date + timedelta(days=rng.randint(1, 3))
    inv_no = f"{['BCE', 'MST', 'RTH', 'GHT', 'DLW'][(n - 1) % 5]}/{2026}/{100 + n * 7:04d}"

    items = []
    for desc, hs, price, unit_kg in rng.sample(GOODS, rng.randint(2, 4)):
        qty = rng.choice([4, 6, 10, 12, 20, 24, 40, 50])
        unit_price = round(price * rng.uniform(0.9, 1.15), 2)
        items.append(
            {
                "description": desc,
                "hs_code": hs,
                "quantity": float(qty),
                "unit_price": unit_price,
                "amount": round(qty * unit_price, 2),
                "_kg": unit_kg * qty,
            }
        )
    net = round(sum(i.pop("_kg") for i in items), 2)
    gross = round(net * 1.08 + 1.5, 2)
    packages = max(1, round(net / 12))
    total = round(sum(i["amount"] for i in items), 2)

    inv_issues: list[dict] = []
    if "inv:missing_hs_code" in problems:
        idx = rng.randrange(len(items))
        items[idx]["hs_code"] = None
        inv_issues.append({"code": "missing_hs_code", "field": f"line_items[{idx}].hs_code"})
    if "inv:total_mismatch" in problems:
        total = round(total + rng.choice([-1, 1]) * rng.choice([50.0, 85.5, 120.0]), 2)
        inv_issues.append({"code": "total_mismatch", "field": "total_value"})
    incoterm = rng.choice(INCOTERMS)
    if "inv:missing_incoterm" in problems:
        incoterm = None
        inv_issues.append({"code": "missing_incoterm", "field": "incoterm"})
    inv_phone = imp[2]
    if "inv:missing_consignee_phone" in problems:
        inv_phone = None
        inv_issues.append({"code": "missing_consignee_phone", "field": "consignee_phone"})

    pid = f"p{n:02d}"
    invoice = Doc(
        doc_id=f"{pid}_invoice",
        doc_type=INVOICE,
        packet_id=pid,
        layout="ABC"[(n - 1) % 3],
        fields={
            "invoice_number": inv_no,
            "invoice_date": inv_date.isoformat(),
            "shipper_name": exp[0],
            "shipper_address": ", ".join(exp[1]),
            "consignee_name": imp[0],
            "consignee_address": ", ".join(imp[1]),
            "consignee_phone": inv_phone,
            "incoterm": incoterm,
            "currency": "USD",
            "total_value": total,
            "gross_weight_kg": gross,
            "net_weight_kg": net,
            "num_packages": packages,
            "line_items": items,
        },
        issues=inv_issues,
    )
    docs = [invoice]
    if n in STANDALONE:
        return docs

    bol_gross = gross
    packet_issues: list[dict] = []
    if "weight_mismatch" in problems:
        bol_gross = round(gross * rng.choice([1.12, 1.2, 0.85]) + 0.5, 2)
        packet_issues.append({"code": "weight_mismatch", "field": "gross_weight_kg"})
    bol_phone = imp[2]
    bol_issues: list[dict] = []
    if "bol:missing_consignee_phone" in problems:
        bol_phone = None
        bol_issues.append({"code": "missing_consignee_phone", "field": "consignee_phone"})
    goods = f"{packages} PACKAGES STC: " + "; ".join(i["description"].upper() for i in items)
    docs.append(
        Doc(
            doc_id=f"{pid}_bol",
            doc_type=BOL,
            packet_id=pid,
            layout="AB"[n % 2],
            fields={
                "bol_number": f"{exp[2][2:]}{imp[3][2:]}{260000 + n * 113}",
                "issue_date": bol_date.isoformat(),
                "shipper_name": exp[0],
                "consignee_name": imp[0],
                "consignee_address": ", ".join(imp[1]),
                "consignee_phone": bol_phone,
                "notify_party": "Same as consignee",
                "vessel_voyage": f"{VESSELS[n % len(VESSELS)]} / {rng.randint(100, 499)}"
                f"{rng.choice('EW')}",
                "port_of_loading": exp[3],
                "port_of_discharge": imp[4],
                "container_number": f"{CONTAINER_PREFIX[n % 5]}{rng.randint(1000000, 9999999)}",
                "num_packages": packages,
                "gross_weight_kg": bol_gross,
                "goods_description": goods,
                "invoice_reference": inv_no,
                "freight_terms": "PREPAID" if incoterm in ("CIF", "CFR", "DAP") else "COLLECT",
            },
            issues=bol_issues,
        )
    )
    for d in docs:  # packet-level issue is recorded on both documents
        d.issues.extend(packet_issues)
    return docs


# ------------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------------


def _fmt_date(iso: str, style: str) -> str:
    d = date.fromisoformat(iso)
    return {"iso": iso, "dmy_text": d.strftime("%d %b %Y"), "dmy_slash": d.strftime("%d/%m/%Y")}[
        style
    ]


def _money(v: float) -> str:
    return f"{v:,.2f}"


class Pen:
    def __init__(self, c: Canvas, y: float = H - 50):
        self.c, self.y = c, y

    def text(
        self, s: str, x: float = 40, size: float = 9.5, bold: bool = False, dy: float = 13
    ) -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.c.drawString(x, self.y, s)
        self.y -= dy

    def gap(self, dy: float = 8) -> None:
        self.y -= dy

    def rule(self) -> None:
        self.c.setLineWidth(0.5)
        self.c.line(40, self.y + 8, W - 40, self.y + 8)
        self.gap(6)


def _party(
    p: Pen,
    label: str,
    name: str,
    addr: str,
    phone: str | None,
    phone_label: str = "Tel:",
    x: float = 40,
) -> None:
    p.text(label, x=x, bold=True)
    p.text(name, x=x)
    for line in addr.split(", ", 1):
        p.text(line, x=x)
    if phone:
        p.text(f"{phone_label} {phone}", x=x)


def _items_table(p: Pen, f: dict, headers: list[str], cols: list[float]) -> None:
    for h, x in zip(headers, cols, strict=True):
        p.c.setFont("Helvetica-Bold", 9)
        p.c.drawString(x, p.y, h)
    p.gap(4)
    p.rule()
    p.gap(4)
    for i, it in enumerate(f["line_items"], 1):
        row = [
            str(i),
            it["description"],
            it["hs_code"] or "",
            f"{it['quantity']:g}",
            _money(it["unit_price"]),
            _money(it["amount"]),
        ]
        p.c.setFont("Helvetica", 9)
        for val, x in zip(row, cols, strict=True):
            p.c.drawString(x, p.y, val)
        p.gap(14)
    p.rule()


def render_invoice(c: Canvas, d: Doc) -> None:
    f, p = d.fields, Pen(c)
    if d.layout == "A":
        p.text("COMMERCIAL INVOICE", size=16, bold=True, dy=24)
        p.text(f"Invoice No.: {f['invoice_number']}")
        p.text(f"Date: {_fmt_date(f['invoice_date'], 'iso')}")
        p.gap()
        _party(p, "Exporter:", f["shipper_name"], f["shipper_address"], None)
        p.gap()
        _party(p, "Consignee:", f["consignee_name"], f["consignee_address"], f["consignee_phone"])
        p.gap()
        if f["incoterm"]:
            p.text(f"Incoterms: {f['incoterm']} 2020")
        p.text(f"Currency: {f['currency']}")
        p.gap(10)
        _items_table(
            p,
            f,
            ["#", "Description", "HS Code", "Qty", "Unit Price", "Amount"],
            [40, 60, 300, 370, 410, 490],
        )
        p.text(f"Total: {f['currency']} {_money(f['total_value'])}", x=380, bold=True)
        p.gap()
        p.text(f"Gross Weight: {f['gross_weight_kg']:.2f} kg")
        p.text(f"Net Weight: {f['net_weight_kg']:.2f} kg")
        p.text(f"No. of Packages: {f['num_packages']}")
    elif d.layout == "B":
        p.text("INVOICE (COMMERCIAL)", size=15, bold=True, dy=22)
        p.text(f"Inv #: {f['invoice_number']}")
        p.text(f"Invoice Date: {_fmt_date(f['invoice_date'], 'dmy_text')}")
        p.gap()
        _party(p, "Seller:", f["shipper_name"], f["shipper_address"], None)
        p.gap()
        _party(
            p,
            "Buyer / Ship To:",
            f["consignee_name"],
            f["consignee_address"],
            f["consignee_phone"],
            phone_label="Phone:",
        )
        p.gap()
        p.text(f"Terms of Delivery: {f['incoterm']}" if f["incoterm"] else "Terms of Delivery:")
        p.gap(10)
        _items_table(
            p,
            f,
            ["Item", "Goods Description", "HS", "Quantity", "Rate", "Total"],
            [40, 70, 300, 365, 420, 490],
        )
        p.text(f"Grand Total ({f['currency']}): {_money(f['total_value'])}", x=340, bold=True)
        p.gap()
        p.text(f"Total Gross Wt.: {f['gross_weight_kg']:.2f} KGS")
        p.text(f"Total Net Wt.: {f['net_weight_kg']:.2f} KGS")
        p.text(f"Packages: {f['num_packages']} cartons")
    else:  # C: side-by-side parties, dd/mm/yyyy dates
        p.text(f["shipper_name"].upper(), size=13, bold=True, dy=16)
        p.text(f["shipper_address"], size=8.5, dy=20)
        p.text("COMMERCIAL INVOICE", x=380, size=13, bold=True, dy=18)
        p.text(f"Invoice Number  {f['invoice_number']}", x=380, dy=12)
        p.text(f"Invoice Date  {_fmt_date(f['invoice_date'], 'dmy_slash')}", x=380, dy=22)
        top = p.y
        _party(p, "SHIPPER / EXPORTER", f["shipper_name"], f["shipper_address"], None)
        left_end = p.y
        p.y = top
        _party(
            p,
            "CONSIGNEE",
            f["consignee_name"],
            f["consignee_address"],
            f["consignee_phone"],
            phone_label="Contact:",
            x=310,
        )
        p.y = min(p.y, left_end) - 8
        p.text(f"Delivery Terms (Incoterms 2020)  {f['incoterm'] or ''}".rstrip())
        p.text(f"Invoice Currency  {f['currency']}")
        p.gap(10)
        _items_table(
            p,
            f,
            ["No", "Description of Goods", "HS Code", "Qty", "Price", "Value"],
            [40, 65, 300, 370, 415, 490],
        )
        p.text(f"TOTAL INVOICE VALUE  {f['currency']} {_money(f['total_value'])}", x=300, bold=True)
        p.gap()
        p.text(f"Gross weight (kg)  {f['gross_weight_kg']:.2f}")
        p.text(f"Net weight (kg)  {f['net_weight_kg']:.2f}")
        p.text(f"Total packages  {f['num_packages']}")
    p.gap(20)
    p.text("We certify that this invoice is true and correct.", size=8)
    p.text("Authorised signatory: ____________________", size=8)
    p.text("SYNTHETIC DOCUMENT - FICTITIOUS DATA FOR SOFTWARE TESTING", size=7)


def render_bol(c: Canvas, d: Doc) -> None:
    f, p = d.fields, Pen(c)
    if d.layout == "A":
        p.text("BILL OF LADING", size=16, bold=True, dy=18)
        p.text("For combined transport or port to port shipment", size=8, dy=20)
        p.text(f"B/L No.: {f['bol_number']}")
        p.text(f"Date of Issue: {_fmt_date(f['issue_date'], 'iso')}")
        p.gap()
        _party(p, "Shipper:", f["shipper_name"], "", None)
        p.gap()
        _party(p, "Consignee:", f["consignee_name"], f["consignee_address"], f["consignee_phone"])
        p.gap()
        p.text(f"Notify Party: {f['notify_party']}")
        p.text(f"Vessel / Voyage: {f['vessel_voyage']}")
        p.text(f"Port of Loading: {f['port_of_loading']}")
        p.text(f"Port of Discharge: {f['port_of_discharge']}")
        p.text(f"Container No.: {f['container_number']}")
        p.gap()
        p.text(f"Number of Packages: {f['num_packages']}")
        p.text(f"Description of Goods: {f['goods_description'][:95]}")
        if len(f["goods_description"]) > 95:
            p.text(f["goods_description"][95:], x=60)
        p.text(f"Gross Weight: {f['gross_weight_kg']:.2f} kg")
        p.gap()
        p.text(f"Freight: {f['freight_terms']}")
        p.text(f"Invoice Ref: {f['invoice_reference']}")
    else:
        p.text("HOUSE BILL OF LADING", size=15, bold=True, dy=22)
        p.text(f"HBL Number: {f['bol_number']}")
        p.text(f"Place and Date of Issue: Origin, {_fmt_date(f['issue_date'], 'dmy_text')}")
        p.gap()
        _party(p, "Exporter:", f["shipper_name"], "", None)
        p.gap()
        _party(
            p,
            "Consigned to:",
            f["consignee_name"],
            f["consignee_address"],
            f["consignee_phone"],
            phone_label="Phone:",
        )
        p.gap()
        p.text(f"Notify: {f['notify_party']}")
        p.text(f"Ocean Vessel: {f['vessel_voyage']}")
        p.text(f"Port of Load: {f['port_of_loading']}")
        p.text(f"Port of Delivery: {f['port_of_discharge']}")
        p.text(f"Container: {f['container_number']}")
        p.gap()
        p.text(f"Packages: {f['num_packages']}")
        p.text(f"Gross Wt: {_money(f['gross_weight_kg'])} KGS")
        p.text(f"Goods: {f['goods_description'][:100]}")
        if len(f["goods_description"]) > 100:
            p.text(f["goods_description"][100:], x=60)
        p.gap()
        p.text(f"Freight Payable: {f['freight_terms']}")
        p.text(f"Commercial Invoice No: {f['invoice_reference']}")
    p.gap(20)
    p.text("SHIPPED on board in apparent good order and condition.", size=8)
    p.text("SYNTHETIC DOCUMENT - FICTITIOUS DATA FOR SOFTWARE TESTING", size=7)


def write_pdf(d: Doc, path: Path) -> None:
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"Synthetic {d.doc_type} {d.doc_id}")
    (render_invoice if d.doc_type == INVOICE else render_bol)(c, d)
    c.showPage()
    c.save()


def rasterise(path: Path, rng: random.Random) -> None:
    """Turn a text PDF into an image-only 'scan' (slight skew + speckle noise)."""
    import pypdfium2 as pdfium
    from PIL import Image

    pdf = pdfium.PdfDocument(str(path))
    img = pdf[0].render(scale=1.6).to_pil().convert("L")
    pdf.close()
    img = img.rotate(
        rng.uniform(-1.2, 1.2), expand=False, fillcolor=255, resample=Image.Resampling.BICUBIC
    )
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 400):
        x, y = rng.randrange(w), rng.randrange(h)
        px[x, y] = rng.randint(90, 200)
    img.save(str(path), "PDF", resolution=144.0, quality=80)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="data/documents")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "pdf").mkdir(parents=True)
    (out / "ground_truth").mkdir()
    rng = random.Random(a.seed)

    docs = [d for n in range(1, N_PACKETS + 1) for d in make_packet(n, rng)]
    for d in docs:
        d.scanned = d.doc_id in SCANNED
        pdf = out / "pdf" / f"{d.doc_id}.pdf"
        write_pdf(d, pdf)
        if d.scanned:
            rasterise(pdf, rng)
        gt = {
            "document_id": d.doc_id,
            "doc_type": d.doc_type,
            "packet_id": d.packet_id,
            "layout": d.layout,
            "scanned": d.scanned,
            "fields": d.fields,
            "issues": d.issues,
        }
        (out / "ground_truth" / f"{d.doc_id}.json").write_text(
            json.dumps(gt, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    manifest = {
        "seed": a.seed,
        "note": "Synthetic, fictitious data generated by scripts/generate_documents.py",
        "documents": [
            {
                "document_id": d.doc_id,
                "doc_type": d.doc_type,
                "packet_id": d.packet_id,
                "layout": d.layout,
                "scanned": d.scanned,
                "issues": sorted({i["code"] for i in d.issues}),
            }
            for d in docs
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    n_issues = sum(1 for d in docs if d.issues)
    print(f"Wrote {len(docs)} documents ({len(SCANNED)} scanned, {n_issues} with issues) to {out}")


if __name__ == "__main__":
    main()

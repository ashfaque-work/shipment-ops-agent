from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mock_carrier.app import create_app
from mock_carrier.scenarios import FLAKY_TRACKING, SCENARIOS, TIMEOUT_TRACKING


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MOCK_CLOCK", "2026-10-01T09:00:00+00:00")
    monkeypatch.setenv("MOCK_TIMEOUT_DELAY_S", "0.05")
    return TestClient(create_app())


GOOD_ADDR = {
    "name": "Adaeze Okafor",
    "street1": "14 Admiralty Way, Lekki Phase 1",
    "city": "Lagos",
    "postal_code": "106104",
    "country": "NG",
    "phone": "+234 803 555 0142",
}
SHIPPER = {
    "company": "Bengal Craft Exports Pvt Ltd",
    "name": "Ritu Sen",
    "street1": "22 Park Street",
    "city": "Kolkata",
    "postal_code": "700016",
    "country": "IN",
    "phone": "+91 33 5555 0101",
}


# ---------- tracking ----------


@pytest.mark.parametrize(
    "tn,status",
    [
        ("MC100004512", "in_transit"),
        ("MC100004513", "delivered"),
        ("MC100004518", "customs_hold"),
        ("MC100004521", "delayed"),
        ("MC100004527", "delivery_failed"),
        ("MC100004533", "out_for_delivery"),
        ("MC100004536", "label_created"),
    ],
)
def test_track_scenarios(client: TestClient, tn: str, status: str) -> None:
    r = client.get(f"/track/{tn}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == status
    assert body["events"], "every scenario has history"
    stamps = [e["timestamp"] for e in body["events"]]
    assert stamps == sorted(stamps, reverse=True), "events are newest first"


def test_track_is_deterministic(client: TestClient) -> None:
    a = client.get("/track/MC100004521").json()
    b = client.get("/track/mc100004521").json()  # case-insensitive
    assert a == b


def test_customs_hold_has_actionable_exception(client: TestClient) -> None:
    body = client.get("/track/MC100004518").json()
    assert body["exception"]["code"] == "CUSTOMS_DOCS_REQUIRED"
    assert "HS code" in body["exception"]["reason"]
    assert body["estimated_delivery"] is None


def test_weather_delay_has_revised_eta(client: TestClient) -> None:
    body = client.get("/track/MC100004521").json()
    assert body["exception"]["code"] == "WEATHER_DELAY"
    assert body["estimated_delivery"] > body["original_estimated_delivery"]


def test_failed_delivery_address_problem(client: TestClient) -> None:
    body = client.get("/track/MC100004527").json()
    assert body["exception"]["code"] == "ADDRESS_INCOMPLETE"


def test_delivered_has_delivered_at(client: TestClient) -> None:
    body = client.get("/track/MC100004513").json()
    assert body["delivered_at"] == body["events"][0]["timestamp"]


def test_unknown_tracking_404(client: TestClient) -> None:
    r = client.get("/track/MC199999999")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_timeout_scenario_returns_504_after_delay(client: TestClient) -> None:
    r = client.get(f"/track/{TIMEOUT_TRACKING}")
    assert r.status_code == 504


def test_flaky_alternates(client: TestClient) -> None:
    codes = [client.get(f"/track/{FLAKY_TRACKING}").status_code for _ in range(4)]
    assert codes == [503, 200, 503, 200]


def test_clock_now_mode(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_CLOCK", "now")
    assert client.get("/track/MC100004512").status_code == 200


def test_all_scenarios_render() -> None:
    from datetime import UTC, datetime

    for s in SCENARIOS.values():
        s.render(datetime(2026, 1, 1, tzinfo=UTC))


# ---------- rates ----------


def test_rates_sorted_and_deterministic(client: TestClient) -> None:
    req = {
        "origin": {"city": "Kolkata", "country": "IN"},
        "destination": {"city": "Nairobi", "country": "KE"},
        "weight_kg": 5,
        "dimensions": {"length_cm": 40, "width_cm": 30, "height_cm": 20},
    }
    a = client.post("/rates", json=req).json()
    b = client.post("/rates", json=req).json()
    assert a == b
    prices = [q["price"] for q in a["quotes"]]
    assert prices == sorted(prices)
    codes = {q["service_code"] for q in a["quotes"]}
    assert codes == {"EXPRESS", "STANDARD", "ECONOMY"}  # freight needs >= 70 kg
    express = next(q for q in a["quotes"] if q["service_code"] == "EXPRESS")
    economy = next(q for q in a["quotes"] if q["service_code"] == "ECONOMY")
    assert express["transit_days_max"] < economy["transit_days_min"]


def test_rates_volumetric_weight_applies(client: TestClient) -> None:
    req = {
        "origin": {"city": "Mumbai", "country": "IN"},
        "destination": {"city": "Lagos", "country": "NG"},
        "weight_kg": 2,
        "dimensions": {"length_cm": 60, "width_cm": 50, "height_cm": 50},  # 30 kg volumetric
    }
    body = client.post("/rates", json=req).json()
    assert body["volumetric_weight_kg"] == 30.0
    assert all(q["chargeable_weight_kg"] == 30.0 for q in body["quotes"])


def test_rates_heavy_includes_freight(client: TestClient) -> None:
    req = {
        "origin": {"city": "Dubai", "country": "AE"},
        "destination": {"city": "Johannesburg", "country": "ZA"},
        "weight_kg": 120,
    }
    codes = [q["service_code"] for q in client.post("/rates", json=req).json()["quotes"]]
    assert "FREIGHT" in codes


def test_rates_unknown_city_422(client: TestClient) -> None:
    req = {
        "origin": {"city": "Atlantis", "country": "IN"},
        "destination": {"city": "Lagos", "country": "NG"},
        "weight_kg": 1,
    }
    r = client.post("/rates", json=req)
    assert r.status_code == 422
    assert r.json()["error"] == "unsupported_location"


def test_rates_invalid_weight_422(client: TestClient) -> None:
    req = {
        "origin": {"city": "Delhi", "country": "IN"},
        "destination": {"city": "Accra", "country": "GH"},
        "weight_kg": -1,
    }
    assert client.post("/rates", json=req).status_code == 422


# ---------- address ----------


def test_address_valid(client: TestClient) -> None:
    body = client.post("/address/validate", json=GOOD_ADDR).json()
    assert body["valid"] is True
    assert body["normalized"]["city"] == "Lagos"


def test_address_misspelled_city_suggests(client: TestClient) -> None:
    body = client.post("/address/validate", json=GOOD_ADDR | {"city": "Lagso"}).json()
    assert body["valid"] is False
    assert any(s["city"] == "Lagos" for s in body["suggestions"])


def test_address_missing_building_number(client: TestClient) -> None:
    body = client.post(
        "/address/validate", json=GOOD_ADDR | {"street1": "Admiralty Way, Lekki"}
    ).json()
    assert body["valid"] is False
    assert any("building" in i for i in body["issues"])


def test_address_required_postcode(client: TestClient) -> None:
    addr = {"street1": "5 Riverside Drive", "city": "Nairobi", "country": "KE"}
    body = client.post("/address/validate", json=addr).json()
    assert body["valid"] is False
    assert any("Postal code is required" in i for i in body["issues"])


def test_address_unsupported_country(client: TestClient) -> None:
    body = client.post("/address/validate", json=GOOD_ADDR | {"country": "FR"}).json()
    assert body["valid"] is False


def test_address_alias_normalised(client: TestClient) -> None:
    addr = {"street1": "1 Marine Drive", "city": "Bombay", "postal_code": "400020", "country": "in"}
    body = client.post("/address/validate", json=addr).json()
    assert body["valid"] is True
    assert body["normalized"]["city"] == "Mumbai"
    assert body["normalized"]["country"] == "IN"


# ---------- shipments ----------


def _shipment(**over) -> dict:
    return {
        "shipper": SHIPPER,
        "recipient": GOOD_ADDR,
        "package": {
            "weight_kg": 3.5,
            "dimensions": {"length_cm": 30, "width_cm": 20, "height_cm": 15},
            "description": "Handwoven cotton scarves",
            "declared_value": 240,
            "currency": "USD",
        },
        "service_code": "STANDARD",
        "reference": "ORD-9001",
    } | over


def test_create_shipment_then_track(client: TestClient) -> None:
    r = client.post("/shipments", json=_shipment())
    assert r.status_code == 201
    body = r.json()
    assert body["tracking_number"].startswith("MC2")
    t = client.get(f"/track/{body['tracking_number']}").json()
    assert t["status"] == "label_created"


def test_create_shipment_idempotent(client: TestClient) -> None:
    a = client.post("/shipments", json=_shipment()).json()
    b = client.post("/shipments", json=_shipment()).json()
    assert a["tracking_number"] == b["tracking_number"]


def test_create_shipment_invalid_address(client: TestClient) -> None:
    r = client.post("/shipments", json=_shipment(recipient=GOOD_ADDR | {"street1": "Lekki"}))
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_address"


def test_create_shipment_unavailable_service(client: TestClient) -> None:
    r = client.post("/shipments", json=_shipment(service_code="FREIGHT"))
    assert r.status_code == 422
    assert r.json()["error"] == "service_unavailable"


def test_admin_reset_clears_created(client: TestClient) -> None:
    tn = client.post("/shipments", json=_shipment()).json()["tracking_number"]
    client.post("/_admin/reset")
    assert client.get(f"/track/{tn}").status_code == 404

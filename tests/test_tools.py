from __future__ import annotations

import pytest

from shipops.agent import tools as T
from shipops.agent.tools import ToolContext


def test_track_by_order_id(ctx: ToolContext) -> None:
    r = T.execute("track_shipment", {"order_id": "#4512"}, ctx)
    assert r["ok"] and r["tracking_number"] == "MC100004512"
    assert r["order"]["customer"] == "Adaeze Okafor"
    assert r["status"] == "in_transit"


def test_track_unknown_order(ctx: ToolContext) -> None:
    r = T.execute("track_shipment", {"order_id": "9999"}, ctx)
    assert not r["ok"] and r["error"] == "order_not_found"


def test_track_unshipped_order(ctx: ToolContext) -> None:
    r = T.execute("track_shipment", {"order_id": "4550"}, ctx)
    assert r["error"] == "not_shipped"


def test_track_timeout_tells_model_not_to_guess(ctx: ToolContext) -> None:
    r = T.execute("track_shipment", {"order_id": "4540"}, ctx)
    assert not r["ok"] and r["error"] == "carrier_timeout"
    assert "Do NOT guess" in r["guidance"]
    assert "status" not in r


def test_track_not_found(ctx: ToolContext) -> None:
    r = T.execute("track_shipment", {"tracking_number": "MC199999999"}, ctx)
    assert r["error"] == "not_found"


def test_compare_rates_infers_country_and_recommends(ctx: ToolContext) -> None:
    r = T.execute(
        "compare_rates",
        {"origin_city": "Kolkata", "destination_city": "Nairobi", "weight_kg": 5},
        ctx,
    )
    assert r["ok"] and len(r["quotes"]) == 3
    assert r["recommendation"]["service_code"] in {"EXPRESS", "STANDARD", "ECONOMY"}
    assert r["recommendation"]["reasons"]


@pytest.mark.parametrize("priority,expected", [("cheapest", "ECONOMY"), ("fastest", "EXPRESS")])
def test_compare_rates_priority(ctx: ToolContext, priority: str, expected: str) -> None:
    r = T.execute(
        "compare_rates",
        {
            "origin_city": "Mumbai",
            "destination_city": "Lagos",
            "weight_kg": 3,
            "priority": priority,
        },
        ctx,
    )
    assert r["recommendation"]["service_code"] == expected


def test_compare_rates_deadline(ctx: ToolContext) -> None:
    r = T.execute(
        "compare_rates",
        {"origin_city": "Delhi", "destination_city": "Accra", "weight_kg": 3, "deadline_days": 9},
        ctx,
    )
    # Standard (6-9 days on this long-haul route) is the cheapest that meets 9 days
    assert r["recommendation"]["service_code"] == "STANDARD"
    assert r["recommendation"]["meets_deadline"] is True
    r = T.execute(
        "compare_rates",
        {"origin_city": "Delhi", "destination_city": "Accra", "weight_kg": 3, "deadline_days": 1},
        ctx,
    )
    assert r["recommendation"]["meets_deadline"] is False


def test_compare_rates_unknown_city(ctx: ToolContext) -> None:
    r = T.execute(
        "compare_rates",
        {"origin_city": "Atlantis", "destination_city": "Lagos", "weight_kg": 1},
        ctx,
    )
    assert r["error"] == "missing_argument"


def test_validate_address_from_order(ctx: ToolContext) -> None:
    assert T.execute("validate_address", {"order_id": "4550"}, ctx)["valid"] is True
    r = T.execute("validate_address", {"order_id": "4551"}, ctx)
    assert r["valid"] is False and any("building" in i for i in r["issues"])


def test_draft_email_for_delay_is_grounded(ctx: ToolContext) -> None:
    r = T.execute("draft_customer_email", {"order_id": "4521"}, ctx)
    d = r["draft"]
    assert d["email_type"] == "delay" and d["to"] == "thandiwe.nkosi@example.com"
    assert "sandstorm" in d["body"] and "Revised estimated delivery" in d["body"]


def test_draft_email_refuses_on_carrier_error(ctx: ToolContext) -> None:
    r = T.execute("draft_customer_email", {"order_id": "4540"}, ctx)
    assert not r["ok"] and "draft" not in r


def test_create_shipment_cannot_execute_directly(ctx: ToolContext) -> None:
    with pytest.raises(PermissionError):
        T.execute("create_shipment", {"order_id": "4550", "service_code": "STANDARD"}, ctx)


def test_preflight_ok_does_not_create(ctx: ToolContext) -> None:
    req, summary = T.preflight_shipment({"order_id": "4550", "service_code": "express"}, ctx)
    assert req is not None
    assert summary["service_code"] == "EXPRESS" and summary["price"] > 0
    assert summary["recipient"]["name"] == "Njeri Mwangi"
    assert ("POST", "/shipments") not in ctx.carrier.calls


def test_preflight_invalid_address(ctx: ToolContext) -> None:
    req, err = T.preflight_shipment({"order_id": "4551", "service_code": "STANDARD"}, ctx)
    assert req is None and err["error"] == "invalid_address"


def test_preflight_already_shipped(ctx: ToolContext) -> None:
    req, err = T.preflight_shipment({"order_id": "4512", "service_code": "STANDARD"}, ctx)
    assert req is None and err["error"] == "already_shipped"


def test_preflight_freight_needs_weight(ctx: ToolContext) -> None:
    req, err = T.preflight_shipment({"order_id": "4550", "service_code": "FREIGHT"}, ctx)
    assert req is None and err["error"] == "service_unavailable"
    req, _ = T.preflight_shipment({"order_id": "4552", "service_code": "FREIGHT"}, ctx)
    assert req is not None


def test_preflight_missing_details(ctx: ToolContext) -> None:
    req, err = T.preflight_shipment(
        {"service_code": "STANDARD", "recipient": {"city": "Lagos"}}, ctx
    )
    assert req is None and err["error"] == "missing_details"


def test_tool_specs_are_well_formed() -> None:
    names = {t["function"]["name"] for t in T.TOOL_SPECS}
    assert names == set(T.EXECUTORS) | T.GATED_TOOLS


def test_validate_address_order_plus_new_fields_checks_the_new_address(ctx: ToolContext) -> None:
    r = T.execute(
        "validate_address",
        {
            "order_id": "4550",
            "street1": "7 Riverside Drive, Westlands",
            "city": "Nairobi",
            "postal_code": "00100",
        },
        ctx,
    )
    assert r["address"]["street1"] == "7 Riverside Drive, Westlands"
    assert r["address"]["name"] == "Njeri Mwangi"  # unspecified fields come from the order
    assert r["valid"] is True
    # an order with a bad stored address can be corrected by passing the new fields
    fixed = T.execute(
        "validate_address", {"order_id": "4551", "street1": "14 Opebi Road, Ikeja"}, ctx
    )
    assert fixed["valid"] is True

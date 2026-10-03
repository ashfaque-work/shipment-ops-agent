from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.api.main import create_app
from shipops.config import Settings
from shipops.llm.baseline import RuleBasedLLM, plan


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Where is order 4512 going to Lagos?", ("track_shipment", {"order_id": "4512"})),
        ("status of MC100004521", ("track_shipment", {"tracking_number": "MC100004521"})),
        (
            "draft a delay email for order 4521",
            ("draft_customer_email", {"order_id": "4521", "email_type": "delay"}),
        ),
        (
            "ship order 4550 with express",
            ("create_shipment", {"order_id": "4550", "service_code": "EXPRESS"}),
        ),
        ("check the address on order 4551", ("validate_address", {"order_id": "4551"})),
        ("analyze doc_ab12cd34", ("analyze_documents", {"document_ids": ["doc_ab12cd34"]})),
    ],
)
def test_baseline_plan(text: str, expected: tuple) -> None:
    calls = plan(text)
    assert calls and calls[0][0] == expected[0]
    for k, v in expected[1].items():
        assert calls[0][1][k] == v


def test_baseline_rates_parsing() -> None:
    ((name, args),) = plan(
        "Cheapest way to send 12.5 kg 40x30x20 from Mumbai to Nairobi within 10 days?"
    )
    assert name == "compare_rates"
    assert args == {
        "origin_city": "Mumbai",
        "destination_city": "Nairobi",
        "weight_kg": 12.5,
        "length_cm": 40.0,
        "width_cm": 30.0,
        "height_cm": 20.0,
        "priority": "cheapest",
        "deadline_days": 10,
    }


def test_baseline_end_to_end_timeout_does_not_guess(ctx: ToolContext) -> None:
    out = AgentService(RuleBasedLLM(), ctx).chat("s", "where is order 4540?")
    assert "won't guess" in out.reply
    for word in ("in transit", "delivered", "customs"):
        assert word not in out.reply.lower()


@pytest.fixture()
def api(ctx: ToolContext) -> TestClient:
    service = AgentService(RuleBasedLLM(), ctx)
    return TestClient(create_app(Settings(llm_provider="offline"), service=service))


def test_api_chat_and_confirm(api: TestClient) -> None:
    assert api.get("/api/health").json()["status"] == "ok"
    r = api.post("/api/chat", json={"message": "Where is order 4518?"}).json()
    assert "customs" in r["reply"].lower() and r["session_id"]
    assert any(t["name"] == "track_shipment" for t in r["trace"] if t["type"] == "tool")

    sid = "sess-1"
    r = api.post(
        "/api/chat", json={"session_id": sid, "message": "ship order 4553 with economy"}
    ).json()
    assert r["reply"] is None and r["pending_confirmation"]["summary"]["service_code"] == "ECONOMY"
    r = api.post("/api/chat/confirm", json={"session_id": sid, "approved": True}).json()
    assert "Tracking number **MC2" in r["reply"]


def test_api_validation(api: TestClient) -> None:
    assert api.post("/api/chat", json={"message": ""}).status_code == 422

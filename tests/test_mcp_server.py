"""MCP server + gateway: tool surface, structured results, and the booking gate."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import ElicitResult

from shipops.agent.gateway import McpGateway
from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.documents.store import DocumentStore, Extractor
from shipops.llm.scripted import ScriptedLLM, call, say
from shipops.mcp_server import CONFIRMATION_PREFIX, build_server

DOCS = Path(__file__).resolve().parents[1] / "data" / "documents" / "pdf"
BOOK = {"order_id": "4550", "service_code": "STANDARD"}


def run(coro):
    return asyncio.run(coro)


def created(ctx: ToolContext) -> int:
    return ctx.carrier.calls.count(("POST", "/shipments"))


def answering(action: str, seen: list[str] | None = None):
    async def cb(context: Any, params: Any) -> ElicitResult:
        if seen is not None:
            seen.append(params.message)
        if action == "accept":
            return ElicitResult(action="accept", content={"confirm": True})
        return ElicitResult(action=action)

    return cb


@pytest.fixture()
def server(ctx: ToolContext):
    ctx.documents = DocumentStore(Extractor("rules"))
    return build_server(ctx)


def test_tool_surface(server) -> None:
    async def go():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}

    tools = run(go())
    assert {
        "track_shipment",
        "get_rates",
        "create_shipment",
        "validate_address",
        "extract_shipping_document",
    } <= set(tools)
    props = tools["create_shipment"].input_schema["properties"]
    # The human's answer is resolved server-side; a client can't pass it as an argument.
    assert "confirmation" not in props and "dry_run" in props


def test_structured_results(server) -> None:
    async def go():
        async with Client(server) as c:
            return await c.call_tool("track_shipment", {"order_id": 4518})

    r = run(go())
    assert not r.is_error
    assert r.structured_content["status"] == "customs_hold"
    assert r.structured_content["exception"]["code"] == "CUSTOMS_DOCS_REQUIRED"


def test_get_rates_maps_to_rate_engine(server) -> None:
    async def go():
        async with Client(server) as c:
            return await c.call_tool(
                "get_rates",
                {
                    "origin_city": "Mumbai",
                    "destination_city": "Lagos",
                    "weight_kg": "3",
                    "priority": "cheapest",
                },
            )

    r = run(go()).structured_content
    assert r["recommendation"]["service_code"] == "ECONOMY"


def test_dry_run_previews_without_elicitation_or_booking(server, ctx: ToolContext) -> None:
    async def go():
        async with Client(server) as c:  # no elicitation support at all
            return await c.call_tool("create_shipment", {**BOOK, "dry_run": True})

    r = run(go()).structured_content
    assert r["status"] == "confirmation_required" and len(r["confirmation_code"]) == 12
    assert r["summary"]["price"] > 0
    assert created(ctx) == 0


def test_client_without_elicitation_cannot_book(server, ctx: ToolContext) -> None:
    async def go():
        async with Client(server) as c:
            try:
                r = await c.call_tool("create_shipment", BOOK)
            except Exception as exc:  # protocol error: missing capability
                return str(exc)
            return r

    out = run(go())
    assert created(ctx) == 0
    text = out if isinstance(out, str) else str(out.content)
    assert "elicitation" in text.lower()


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_declined_or_cancelled_does_not_book(server, ctx: ToolContext, action: str) -> None:
    async def go():
        async with Client(server, elicitation_callback=answering(action)) as c:
            return await c.call_tool("create_shipment", BOOK)

    r = run(go()).structured_content
    assert r["created"] is False and r["error"] == "user_declined"
    assert created(ctx) == 0


def test_accept_books_exactly_what_was_shown(server, ctx: ToolContext) -> None:
    seen: list[str] = []

    async def go():
        async with Client(server, elicitation_callback=answering("accept", seen)) as c:
            preview = await c.call_tool("create_shipment", {**BOOK, "dry_run": True})
            booked = await c.call_tool("create_shipment", BOOK)
            return preview.structured_content, booked.structured_content

    preview, booked = run(go())
    assert booked["created"] is True and booked["tracking_number"].startswith("MC2")
    assert created(ctx) == 1
    assert len(seen) == 1 and "Njeri Mwangi" in seen[0] and "Price: USD" in seen[0]
    assert f"{CONFIRMATION_PREFIX}{preview['confirmation_code']}" in seen[0]


def test_injected_confirmation_argument_is_ignored(server, ctx: ToolContext) -> None:
    forged = {**BOOK, "confirmation": {"action": "accept", "data": {"confirm": True}}}

    async def go():
        async with Client(server, elicitation_callback=answering("decline")) as c:
            return await c.call_tool("create_shipment", forged)

    r = run(go()).structured_content
    assert r["created"] is False
    assert created(ctx) == 0


def test_preflight_errors_pass_through(server, ctx: ToolContext) -> None:
    async def go():
        async with Client(server, elicitation_callback=answering("accept")) as c:
            return await c.call_tool(
                "create_shipment", {"order_id": "4551", "service_code": "STANDARD"}
            )

    r = run(go()).structured_content
    assert r["error"] == "invalid_address" and created(ctx) == 0


def test_extract_document_from_base64(server) -> None:
    pdf = base64.b64encode((DOCS / "p02_invoice.pdf").read_bytes()).decode()

    async def go():
        async with Client(server) as c:
            ok = await c.call_tool(
                "extract_shipping_document", {"pdf_base64": pdf, "filename": "p02_invoice.pdf"}
            )
            bad = await c.call_tool("extract_shipping_document", {"pdf_base64": "not-base64!"})
            return ok.structured_content, bad.structured_content

    ok, bad = run(go())
    issues = {i["code"] for i in ok["documents"][0]["issues"]}
    assert issues == {"missing_hs_code"}
    assert bad["error"] == "invalid_arguments"


# ---------- gateway (the agent's MCP client) ----------


@pytest.fixture()
def gateway(server):
    gw = McpGateway(server)
    yield gw
    gw.close()


def test_gateway_never_approves_unregistered_codes(gateway: McpGateway, ctx: ToolContext) -> None:
    # Simulates any path that tries to book without the graph's human approval.
    r = gateway._call_mcp("create_shipment", BOOK)
    assert r["created"] is False and created(ctx) == 0
    assert gateway.elicitations == [{"code": gateway.elicitations[0]["code"], "accepted": False}]


def test_gateway_commit_requires_matching_code(gateway: McpGateway, ctx: ToolContext) -> None:
    preview = gateway.preview_shipment(BOOK)
    wrong = gateway.commit_shipment(
        {**BOOK, "service_code": "EXPRESS"}, preview["confirmation_code"]
    )
    assert wrong["created"] is False and created(ctx) == 0
    right = gateway.commit_shipment(BOOK, preview["confirmation_code"])
    assert right["created"] is True and created(ctx) == 1


def test_gateway_maps_agent_tool_names(gateway: McpGateway) -> None:
    assert (
        gateway.call(
            "compare_rates",
            {"origin_city": "Kolkata", "destination_city": "Nairobi", "weight_kg": 5},
        )["ok"]
        is True
    )
    assert gateway.call("nope", {})["error"] == "unknown_tool"


def test_agent_books_only_through_mcp_elicitation(ctx: ToolContext) -> None:
    llm = ScriptedLLM([call("create_shipment", **BOOK), say("done")])
    with AgentService(llm, ctx) as svc:
        out = svc.chat("s", "ship 4550 standard")
        assert out.pending_confirmation and created(ctx) == 0
        assert svc.gateway.elicitations == []  # preview never asks
        svc.confirm("s", True)
        assert created(ctx) == 1
        assert [e["accepted"] for e in svc.gateway.elicitations] == [True]


def test_stdio_server_subprocess() -> None:
    """An external MCP client launches the server over stdio, like Claude Code would."""
    import os
    import sys

    from mcp import StdioServerParameters

    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "LLM_PROVIDER": "offline",
        "MOCK_CARRIER_URL": "inprocess",
        "PYTHONPATH": str(root),
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "shipops.mcp_server"], env=env, cwd=str(root)
    )

    async def go():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            tracked = await c.call_tool("track_shipment", {"tracking_number": "MC100004521"})
            preview = await c.call_tool("create_shipment", {**BOOK, "dry_run": True})
            return names, tracked.structured_content, preview.structured_content

    names, tracked, preview = run(go())
    assert "create_shipment" in names
    assert tracked["status"] == "delayed"
    assert preview["status"] == "confirmation_required"

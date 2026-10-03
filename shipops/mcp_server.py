"""MCP server exposing the shipping tools (official MCP Python SDK v2).

The agent consumes every tool through this server (in-process, see agent/gateway.py), and
any MCP client can connect to it over stdio or Streamable HTTP:

    uv run python -m shipops.mcp_server                       # stdio (Claude Code/Desktop)
    uv run python -m shipops.mcp_server --transport streamable-http --port 8002

`create_shipment` is irreversible, so it is gated by MCP *elicitation*: the server shows
the user a summary with a confirmation code and books only on an explicit "accept" whose
code matches what was shown. Clients without elicitation support cannot book at all
(fail closed); `dry_run=true` previews without booking. Tool implementations live in
shipops/agent/tools.py - this module is the protocol boundary only.
"""

# No `from __future__ import annotations` here: the MCP SDK evaluates tool signatures, and
# the confirmation resolver is a closure referenced from an annotation.
import argparse
import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from pydantic import BaseModel, Field

from shipops.agent import tools as T
from shipops.carriers.models import ShipmentRequest

log = logging.getLogger(__name__)

SERVER_NAME = "shipment-ops"
CONFIRMATION_PREFIX = "Confirmation code: "

Num = float | int | str | None  # LLMs send numbers as strings too; executors normalise
Id = str | int | None


class BookingConfirmation(BaseModel):
    confirm: bool = Field(description="True to book this shipment and buy the carrier label")


def request_digest(req: ShipmentRequest) -> str:
    """Short code identifying the exact request a human is asked to approve."""
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()[:12]


def _args(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _summary_text(summary: dict[str, Any], code: str) -> str:
    r, p = summary["recipient"], summary["package"]
    address = ", ".join(
        str(x)
        for x in (r.get("street1"), r.get("city"), r.get("postal_code"), r.get("country"))
        if x
    )
    return (
        "Book this shipment? This buys a carrier label and cannot be undone.\n"
        f"Ship to: {r.get('name')}, {address}\n"
        f"Package: {p.get('description')}, {p.get('weight_kg')} kg, "
        f"declared {p.get('currency')} {p.get('declared_value')}\n"
        f"Service: {summary['service_name']}, {summary['transit_days']} days, "
        f"est. delivery {summary['estimated_delivery']}\n"
        f"Price: {summary['currency']} {summary['price']:.2f}\n"
        f"{CONFIRMATION_PREFIX}{code}"
    )


def build_server(ctx: T.ToolContext) -> MCPServer:
    """Build the MCP server around a ToolContext (carrier adapter + document store)."""
    server = MCPServer(
        SERVER_NAME,
        title="Shipment Ops",
        instructions=(
            "Shipping operations tools backed by a carrier adapter (the bundled mock carrier "
            "by default). create_shipment is irreversible: it asks the user to confirm via "
            "elicitation; use dry_run=true to preview."
        ),
        log_level="WARNING",
    )
    # args fingerprint -> code shown to the human; lets the booking verify that what is
    # committed is exactly what was confirmed (bounded, newest last).
    shown: OrderedDict[str, str] = OrderedDict()
    lock = threading.Lock()

    def fingerprint(args: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()

    @server.tool(description=T.TOOL_SPECS[0]["function"]["description"])
    def track_shipment(tracking_number: Id = None, order_id: Id = None) -> dict[str, Any]:
        return T.EXECUTORS["track_shipment"](
            _args(tracking_number=tracking_number, order_id=order_id), ctx
        )

    @server.tool(
        description="Look up a store order: customer, delivery address, items, "
        "value, package size and whether it has shipped."
    )
    def get_order(order_id: Id = None) -> dict[str, Any]:
        return T.EXECUTORS["get_order"](_args(order_id=order_id), ctx)

    @server.tool(
        description="Get shipping quotes for a route and parcel plus a recommended "
        "service with reasons. Countries are ISO-2 codes (inferred from "
        "well-known cities if omitted). priority: balanced | cheapest | "
        "fastest."
    )
    def get_rates(
        origin_city: str | None = None,
        destination_city: str | None = None,
        weight_kg: Num = None,
        origin_country: str | None = None,
        destination_country: str | None = None,
        length_cm: Num = None,
        width_cm: Num = None,
        height_cm: Num = None,
        priority: str | None = None,
        deadline_days: Num = None,
    ) -> dict[str, Any]:
        return T.EXECUTORS["compare_rates"](
            _args(
                origin_city=origin_city,
                destination_city=destination_city,
                weight_kg=weight_kg,
                origin_country=origin_country,
                destination_country=destination_country,
                length_cm=length_cm,
                width_cm=width_cm,
                height_cm=height_cm,
                priority=priority,
                deadline_days=deadline_days,
            ),
            ctx,
        )

    @server.tool(
        description="Check a delivery address with the carrier; returns problems "
        "and suggestions. Pass order_id to check an order's address."
    )
    def validate_address(
        name: str | None = None,
        street1: str | None = None,
        street2: str | None = None,
        city: str | None = None,
        postal_code: Id = None,
        country: str | None = None,
        phone: str | None = None,
        order_id: Id = None,
    ) -> dict[str, Any]:
        return T.EXECUTORS["validate_address"](
            _args(
                name=name,
                street1=street1,
                street2=street2,
                city=city,
                postal_code=postal_code,
                country=country,
                phone=phone,
                order_id=order_id,
            ),
            ctx,
        )

    @server.tool(
        description="Draft (not send) a customer email from live tracking data. "
        "email_type: auto | delay | customs_hold | delivery_failed | "
        "delivered | in_transit."
    )
    def draft_customer_email(
        tracking_number: Id = None,
        order_id: Id = None,
        email_type: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        return T.EXECUTORS["draft_customer_email"](
            _args(
                tracking_number=tracking_number, order_id=order_id, email_type=email_type, note=note
            ),
            ctx,
        )

    @server.tool(
        description="Extract structured fields from shipping documents (commercial "
        "invoice, bill of lading) and flag missing or inconsistent fields, "
        "including invoice/BoL cross-checks. Pass ids of uploaded "
        "documents, and/or a PDF as base64 (max ~5 MB)."
    )
    def extract_shipping_document(
        document_ids: list[str] | str | None = None,
        pdf_base64: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        args = _args(document_ids=document_ids)
        if pdf_base64:
            import base64
            import binascii

            if ctx.documents is None:
                return T.error_result("unavailable", "Document analysis is not configured.")
            try:
                data = base64.b64decode(pdf_base64, validate=True)
            except (binascii.Error, ValueError):
                return T.error_result("invalid_arguments", "pdf_base64 is not valid base64.")
            if len(data) > 6 * 1024 * 1024 or not data.startswith(b"%PDF"):
                return T.error_result("invalid_arguments", "Expected a PDF of at most ~5 MB.")
            rec = ctx.documents.add(filename or "upload.pdf", data)
            ids = args.get("document_ids") or []
            args["document_ids"] = ([ids] if isinstance(ids, str) else list(ids)) + [
                rec["document_id"]
            ]
        return T.EXECUTORS["analyze_documents"](args, ctx)

    async def confirm_booking(
        order_id: Id = None,
        service_code: str | None = None,
        recipient: dict[str, Any] | None = None,
        package: dict[str, Any] | None = None,
        reference: str | None = None,
        dry_run: bool = False,
    ) -> BookingConfirmation | Elicit[BookingConfirmation]:
        """Resolver: never confirms by itself; only a client answer can say yes."""
        if dry_run:
            return BookingConfirmation(confirm=False)
        args = _args(
            order_id=order_id,
            service_code=service_code,
            recipient=recipient,
            package=package,
            reference=reference,
        )
        req, summary = await anyio.to_thread.run_sync(T.preflight_shipment, args, ctx)
        if req is None:  # nothing bookable; the tool body reports the problem
            return BookingConfirmation(confirm=False)
        code = request_digest(req)
        with lock:
            shown[fingerprint(args)] = code
            while len(shown) > 256:
                shown.popitem(last=False)
        return Elicit(_summary_text(summary, code), BookingConfirmation)

    @server.tool(
        description=(
            "Book a shipment and buy a label (IRREVERSIBLE, costs money). The user is asked to "
            "confirm via elicitation before anything is booked; clients without elicitation "
            "support cannot book. dry_run=true returns the priced preview and a confirmation "
            "code without booking. Use order_id to take recipient and package from the order; "
            "service_code: EXPRESS | STANDARD | ECONOMY | FREIGHT."
        )
    )
    def create_shipment(
        confirmation: Annotated[ElicitationResult[BookingConfirmation], Resolve(confirm_booking)],
        order_id: Id = None,
        service_code: str | None = None,
        recipient: dict[str, Any] | None = None,
        package: dict[str, Any] | None = None,
        reference: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        args = _args(
            order_id=order_id,
            service_code=service_code,
            recipient=recipient,
            package=package,
            reference=reference,
        )
        req, summary = T.preflight_shipment(args, ctx)
        if req is None:
            return summary  # the preflight error (bad address, unknown service, ...)
        code = request_digest(req)
        if dry_run:
            return {
                "ok": True,
                "status": "confirmation_required",
                "confirmation_code": code,
                "summary": summary,
            }
        approved = (
            isinstance(confirmation, AcceptedElicitation)
            and confirmation.data is not None
            and confirmation.data.confirm is True
        )
        if not approved:
            return {
                "ok": False,
                "created": False,
                "error": "user_declined",
                "message": "The user did not confirm. The shipment was NOT created.",
            }
        with lock:
            shown_code = shown.pop(fingerprint(args), None)
        if shown_code != code:  # details changed between the question and the answer
            return {
                "ok": False,
                "created": False,
                "error": "confirmation_mismatch",
                "message": "Shipment details changed after confirmation was requested. "
                "Nothing was booked; please confirm again.",
            }
        return T.commit_shipment(req, ctx)

    return server


def server_from_settings() -> MCPServer:
    """Standalone server: carrier from CARRIER / MOCK_CARRIER_URL, document extraction from
    the configured LLM keys (rule-based parser without keys)."""
    from shipops.carriers import get_carrier
    from shipops.config import get_settings
    from shipops.documents import DocumentStore, get_extractor

    settings = get_settings()
    ctx = T.ToolContext(
        carrier=get_carrier(settings), documents=DocumentStore(get_extractor(settings))
    )
    return build_server(ctx)


def main() -> None:
    ap = argparse.ArgumentParser(description="Shipment Ops MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    a = ap.parse_args()
    server = server_from_settings()
    if a.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=a.host, port=a.port)


if __name__ == "__main__":
    main()

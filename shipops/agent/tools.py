"""Agent tools: JSON schemas for the LLM + executors that call the carrier adapter.

Every executor returns a JSON-serialisable dict with an `ok` flag. Errors carry a
stable `error` code plus `guidance` telling the model how to respond honestly.

`create_shipment` is GATED: it is never executed here. The graph runs a *preflight*
(address validation + price quote) and then pauses for explicit human confirmation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shipops import orders
from shipops.agent import emails
from shipops.carriers.base import CarrierAdapter, CarrierError, CarrierNotFound
from shipops.carriers.models import (
    Address,
    Dimensions,
    Location,
    Package,
    RateQuote,
    RateRequest,
    ShipmentRequest,
    TrackingInfo,
)
from shipops.geo import country_for

GATED_TOOLS = frozenset({"create_shipment"})

NO_GUESS = (
    "Do NOT guess or invent a shipment status, location or date. Tell the user the "
    "information is unavailable right now and suggest retrying later."
)


@dataclass
class ToolContext:
    carrier: CarrierAdapter
    documents: Any = None  # DocumentStore (phase 3); optional
    extra: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------------
# Schemas (OpenAI function-calling format)
# ----------------------------------------------------------------------------------

_ADDRESS_PROPS = {
    "name": {"type": "string"},
    "street1": {"type": "string", "description": "Street address incl. building number"},
    "street2": {"type": "string"},
    "city": {"type": "string"},
    "postal_code": {"type": "string"},
    "country": {"type": "string", "description": "ISO 3166-1 alpha-2, e.g. NG, KE, GH, ZA, IN"},
    "phone": {"type": "string"},
}


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _fn(
        "track_shipment",
        "Get the live carrier tracking status, location, ETA and event history of a "
        "shipment. Accepts a carrier tracking number (e.g. MC100004512) OR a store order "
        "number (e.g. 4512).",
        {
            "tracking_number": {"type": "string"},
            "order_id": {"type": "string", "description": "Store order number, digits only"},
        },
    ),
    _fn(
        "get_order",
        "Look up a store order: customer, delivery address, items, value, package size "
        "and whether it has shipped. Use before creating a shipment for an order.",
        {"order_id": {"type": "string"}},
        ["order_id"],
    ),
    _fn(
        "compare_rates",
        "Get shipping quotes for a route and parcel, and a recommended service with "
        "reasons. Countries are ISO-2 codes (inferred from well-known cities if omitted).",
        {
            "origin_city": {"type": "string"},
            "origin_country": {"type": "string"},
            "destination_city": {"type": "string"},
            "destination_country": {"type": "string"},
            "weight_kg": {"type": "number"},
            "length_cm": {"type": "number"},
            "width_cm": {"type": "number"},
            "height_cm": {"type": "number"},
            "priority": {
                "type": "string",
                "enum": ["balanced", "cheapest", "fastest"],
                "description": "What the user cares about most (default balanced)",
            },
            "deadline_days": {
                "type": "integer",
                "description": "Must arrive within this many days, if stated",
            },
        },
        ["origin_city", "destination_city", "weight_kg"],
    ),
    _fn(
        "validate_address",
        "Check a delivery address with the carrier; returns problems and suggestions. "
        "Pass order_id to validate the address stored on an order.",
        {**_ADDRESS_PROPS, "order_id": {"type": "string"}},
    ),
    _fn(
        "create_shipment",
        "Book a shipment and buy a label (IRREVERSIBLE, costs money). The system will "
        "show the user a summary and ask them to confirm before anything is booked; you do "
        "not need to ask in text. Provide order_id to use the order's recipient and "
        "package details, or give recipient/package explicitly. Shipper defaults to our "
        "Kolkata warehouse.",
        {
            "order_id": {"type": "string"},
            "service_code": {
                "type": "string",
                "enum": ["EXPRESS", "STANDARD", "ECONOMY", "FREIGHT"],
            },
            "recipient": {"type": "object", "properties": _ADDRESS_PROPS},
            "package": {
                "type": "object",
                "properties": {
                    "weight_kg": {"type": "number"},
                    "length_cm": {"type": "number"},
                    "width_cm": {"type": "number"},
                    "height_cm": {"type": "number"},
                    "description": {"type": "string"},
                    "declared_value": {"type": "number"},
                    "currency": {"type": "string"},
                },
            },
            "reference": {"type": "string"},
        },
        ["service_code"],
    ),
    _fn(
        "draft_customer_email",
        "Draft (not send) a customer email based on live tracking data, e.g. a delay "
        "notification, customs hold notice or failed-delivery request. email_type 'auto' "
        "picks the template from the current status.",
        {
            "tracking_number": {"type": "string"},
            "order_id": {"type": "string"},
            "email_type": {"type": "string", "enum": list(emails.EMAIL_TYPES)},
            "note": {"type": "string", "description": "Optional extra sentence to append"},
        },
    ),
    _fn(
        "analyze_documents",
        "Extract structured fields from uploaded shipping documents (commercial invoice, "
        "bill of lading) and flag missing or inconsistent fields, including cross-document "
        "mismatches. Use the document ids shown when files were uploaded.",
        {"document_ids": {"type": "array", "items": {"type": "string"}}},
        ["document_ids"],
    ),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]


# ----------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------


def error_result(code: str, message: str, guidance: str | None = None, **extra: Any) -> dict:
    out = {"ok": False, "error": code, "message": message}
    if guidance:
        out["guidance"] = guidance
    return out | extra


def _carrier_err(exc: CarrierError, *, tracking: bool = False) -> dict:
    guidance = None
    if tracking and not isinstance(exc, CarrierNotFound):
        guidance = NO_GUESS
    elif isinstance(exc, CarrierNotFound):
        guidance = "Tell the user no shipment was found and ask them to double-check the number."
    return error_result(
        exc.code,
        exc.message,
        guidance,
        **({"details": exc.details} if exc.details and not tracking else {}),
    )


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


def _tracking_summary(info: TrackingInfo, order: orders.Order | None) -> dict:
    out: dict[str, Any] = {
        "ok": True,
        "tracking_number": info.tracking_number,
        "status": info.status,
        "status_detail": info.status_detail,
        "service": info.service_code,
        "origin": info.origin,
        "destination": info.destination,
        "current_location": info.current_location,
        "estimated_delivery": _iso(info.estimated_delivery),
    }
    if info.original_estimated_delivery:
        out["original_estimated_delivery"] = _iso(info.original_estimated_delivery)
    if info.delivered_at:
        out["delivered_at"] = _iso(info.delivered_at)
    if info.exception:
        out["exception"] = info.exception.model_dump()
    out["recent_events"] = [
        {"time": _iso(e.timestamp), "location": e.location, "event": e.description}
        for e in info.events[:5]
    ]
    if order:
        out["order"] = {"order_id": order.order_id, "customer": order.customer.name}
    return out


def _resolve_tracking(args: dict) -> tuple[str | None, orders.Order | None, dict | None]:
    tn = (args.get("tracking_number") or "").strip().upper() or None
    oid = args.get("order_id")
    order = None
    if oid:
        order = orders.get_order(oid)
        if order is None:
            return (
                None,
                None,
                error_result(
                    "order_not_found",
                    f"No order #{oid} in the order book.",
                    "Ask the user to check the order number.",
                ),
            )
        if not order.tracking_number:
            return (
                None,
                order,
                error_result(
                    "not_shipped",
                    f"Order #{order.order_id} has not shipped yet (status: {order.status}).",
                    "Offer to compare rates or create a shipment.",
                ),
            )
        tn = tn or order.tracking_number
    if not tn:
        return (
            None,
            None,
            error_result("missing_argument", "Provide a tracking_number or order_id."),
        )
    return tn, order or orders.find_order_by_tracking(tn), None


def recommend(
    quotes: list[RateQuote], priority: str = "balanced", deadline_days: int | None = None
) -> dict:
    pool = quotes
    reasons: list[str] = []
    if deadline_days:
        pool = [q for q in quotes if q.transit_days_max <= deadline_days]
        if not pool:
            fastest = min(quotes, key=lambda q: q.transit_days_max)
            return {
                "service_code": fastest.service_code,
                "reasons": [
                    f"No service guarantees delivery within {deadline_days} days; "
                    f"{fastest.service_name} is the fastest at "
                    f"{fastest.transit_days_min}-{fastest.transit_days_max} days."
                ],
                "meets_deadline": False,
            }
        reasons.append(
            f"Meets the {deadline_days}-day deadline ({len(pool)} of {len(quotes)} services do)"
        )
        priority = "cheapest" if priority == "balanced" else priority

    cheapest = min(pool, key=lambda q: q.price)
    fastest = min(pool, key=lambda q: (q.transit_days_max, q.price))
    if priority == "cheapest":
        best = cheapest
    elif priority == "fastest":
        best = fastest
    else:
        pmin, pmax = cheapest.price, max(q.price for q in pool)
        tmin = fastest.transit_days_max
        tmax = max(q.transit_days_max for q in pool)

        def score(q: RateQuote) -> float:
            p = (q.price - pmin) / (pmax - pmin) if pmax > pmin else 0
            t = (q.transit_days_max - tmin) / (tmax - tmin) if tmax > tmin else 0
            return 0.5 * p + 0.5 * t

        best = min(pool, key=lambda q: (score(q), q.price))
        reasons.append("Best balance of price and speed among the available services")

    reasons.append(
        f"{best.service_name}: {best.currency} {best.price:.2f}, "
        f"{best.transit_days_min}-{best.transit_days_max} days "
        f"(est. delivery {best.estimated_delivery.isoformat()})"
    )
    if best is cheapest:
        reasons.append("Lowest price of the eligible options")
    else:
        reasons.append(
            f"Costs {best.currency} {best.price - cheapest.price:.2f} more than "
            f"{cheapest.service_name} but arrives up to "
            f"{cheapest.transit_days_max - best.transit_days_max} days sooner"
        )
    if best is fastest and best is not cheapest:
        reasons.append("Fastest eligible option")
    return {
        "service_code": best.service_code,
        "reasons": reasons,
        **({"meets_deadline": True} if deadline_days else {}),
    }


# ----------------------------------------------------------------------------------
# Executors (non-gated)
# ----------------------------------------------------------------------------------


def track_shipment(args: dict, ctx: ToolContext) -> dict:
    tn, order, error = _resolve_tracking(args)
    if error:
        return error
    try:
        info = ctx.carrier.track(tn)
    except CarrierError as exc:
        return _carrier_err(exc, tracking=True) | {"tracking_number": tn}
    return _tracking_summary(info, order)


def get_order(args: dict, ctx: ToolContext) -> dict:
    order = orders.get_order(args.get("order_id", ""))
    if order is None:
        return error_result(
            "order_not_found", f"No order #{args.get('order_id')} in the order book."
        )
    return {"ok": True, **order.model_dump(mode="json", exclude_none=True)}


def compare_rates(args: dict, ctx: ToolContext) -> dict:
    try:
        o_city, d_city = args["origin_city"], args["destination_city"]
        o_country = country_for(o_city, args.get("origin_country"))
        d_country = country_for(d_city, args.get("destination_country"))
        if not o_country or not d_country:
            missing = o_city if not o_country else d_city
            return error_result(
                "missing_argument",
                f"Unknown country for '{missing}'.",
                "Ask the user which country the city is in.",
            )
        dims = None
        if all(args.get(k) for k in ("length_cm", "width_cm", "height_cm")):
            dims = Dimensions(
                length_cm=args["length_cm"], width_cm=args["width_cm"], height_cm=args["height_cm"]
            )
        req = RateRequest(
            origin=Location(city=o_city, country=o_country),
            destination=Location(city=d_city, country=d_country),
            weight_kg=float(args["weight_kg"]),
            dimensions=dims,
        )
    except (KeyError, ValueError, ValidationError) as exc:
        return error_result("invalid_arguments", f"Could not build a rate request: {exc}")
    try:
        res = ctx.carrier.get_rates(req)
    except CarrierError as exc:
        return _carrier_err(exc)
    if not res.quotes:
        return error_result("no_services", "The carrier offers no service for this shipment.")
    rec = recommend(res.quotes, args.get("priority") or "balanced", args.get("deadline_days"))
    return {
        "ok": True,
        "route": f"{res.origin} -> {res.destination}",
        "distance_km": res.distance_km,
        "chargeable_weight_kg": res.quotes[0].chargeable_weight_kg,
        "volumetric_weight_kg": res.volumetric_weight_kg,
        "quotes": [
            {
                "service_code": q.service_code,
                "service": q.service_name,
                "price": q.price,
                "currency": q.currency,
                "transit_days": f"{q.transit_days_min}-{q.transit_days_max}",
                "estimated_delivery": q.estimated_delivery.isoformat(),
            }
            for q in res.quotes
        ],
        "recommendation": rec,
    }


def validate_address(args: dict, ctx: ToolContext) -> dict:
    if args.get("order_id"):
        order = orders.get_order(args["order_id"])
        if order is None:
            return error_result(
                "order_not_found", f"No order #{args['order_id']} in the order book."
            )
        addr = order.recipient_address()
    else:
        fields = {k: v for k, v in args.items() if k in _ADDRESS_PROPS and v}
        if fields.get("city") and not fields.get("country"):
            fields["country"] = country_for(fields["city"]) or ""
        addr = Address(**fields)
    try:
        res = ctx.carrier.validate_address(addr)
    except CarrierError as exc:
        return _carrier_err(exc)
    return {
        "ok": True,
        "address": addr.model_dump(exclude_none=True),
        **res.model_dump(mode="json", exclude_none=True),
    }


def draft_customer_email(args: dict, ctx: ToolContext) -> dict:
    tn, order, error = _resolve_tracking(args)
    if error:
        return error
    try:
        info = ctx.carrier.track(tn)
    except CarrierError as exc:
        return _carrier_err(exc, tracking=True) | {
            "guidance": NO_GUESS + " Do not draft an email with made-up status details."
        }
    return {
        "ok": True,
        "draft": emails.draft(info, order, args.get("email_type") or "auto", args.get("note")),
    }


def analyze_documents(args: dict, ctx: ToolContext) -> dict:
    if ctx.documents is None:
        return error_result("unavailable", "Document analysis is not configured.")
    ids = args.get("document_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    return ctx.documents.analyze(ids)


EXECUTORS = {
    "track_shipment": track_shipment,
    "get_order": get_order,
    "compare_rates": compare_rates,
    "validate_address": validate_address,
    "draft_customer_email": draft_customer_email,
    "analyze_documents": analyze_documents,
}


def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    if name in GATED_TOOLS:
        raise PermissionError(f"{name} requires human confirmation and cannot run directly")
    fn = EXECUTORS.get(name)
    if fn is None:
        return error_result("unknown_tool", f"There is no tool called {name}.")
    try:
        return fn(args, ctx)
    except CarrierError as exc:  # defensive: executors should already map these
        return _carrier_err(exc)


def parse_args(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


# ----------------------------------------------------------------------------------
# create_shipment: preflight (safe) + commit (only after human approval)
# ----------------------------------------------------------------------------------


def build_shipment_request(args: dict) -> ShipmentRequest | dict:
    """Merge order defaults with explicit arguments. Returns an error dict on failure."""
    order = None
    if args.get("order_id"):
        order = orders.get_order(args["order_id"])
        if order is None:
            return error_result(
                "order_not_found", f"No order #{args['order_id']} in the order book."
            )
        if order.tracking_number:
            return error_result(
                "already_shipped",
                f"Order #{order.order_id} already shipped as {order.tracking_number}.",
                "Do not create a duplicate shipment; offer to track it instead.",
            )

    recipient = order.recipient_address().model_dump() if order else {}
    recipient |= {k: v for k, v in (args.get("recipient") or {}).items() if v}
    if recipient.get("city") and not recipient.get("country"):
        recipient["country"] = country_for(recipient["city"]) or ""

    pkg: dict[str, Any] = {}
    if order and order.package:
        p = order.package
        pkg = {
            "weight_kg": p.weight_kg,
            "length_cm": p.length_cm,
            "width_cm": p.width_cm,
            "height_cm": p.height_cm,
            "description": p.description,
            "declared_value": order.value,
            "currency": order.currency,
        }
    pkg |= {k: v for k, v in (args.get("package") or {}).items() if v not in (None, "")}

    missing = [k for k in ("name", "street1", "city", "country") if not recipient.get(k)]
    missing += [f"package.{k}" for k in ("weight_kg", "description") if not pkg.get(k)]
    if missing:
        return error_result(
            "missing_details",
            "Missing shipment details: " + ", ".join(missing),
            "Ask the user for the missing details before trying again.",
        )
    dims = None
    if all(pkg.get(k) for k in ("length_cm", "width_cm", "height_cm")):
        dims = Dimensions(
            length_cm=pkg["length_cm"], width_cm=pkg["width_cm"], height_cm=pkg["height_cm"]
        )
    try:
        return ShipmentRequest(
            shipper=orders.business_address(),
            recipient=Address(**{k: v for k, v in recipient.items() if k in Address.model_fields}),
            package=Package(
                weight_kg=float(pkg["weight_kg"]),
                dimensions=dims,
                description=str(pkg["description"]),
                declared_value=float(pkg.get("declared_value") or 0),
                currency=pkg.get("currency") or "USD",
            ),
            service_code=str(args.get("service_code") or "").upper(),
            reference=args.get("reference") or (f"ORD-{order.order_id}" if order else None),
        )
    except (ValidationError, ValueError) as exc:
        return error_result("invalid_arguments", f"Invalid shipment details: {exc}")


def preflight_shipment(args: dict, ctx: ToolContext) -> tuple[ShipmentRequest | None, dict]:
    """Validate + price a shipment WITHOUT creating it.

    Returns (request, summary) when ready for human confirmation, else (None, error dict).
    """
    req = build_shipment_request(args)
    if isinstance(req, dict):
        return None, req
    try:
        check = ctx.carrier.validate_address(req.recipient)
        if not check.valid:
            return None, error_result(
                "invalid_address",
                "Recipient address failed validation: " + "; ".join(check.issues),
                "Explain the problems to the user and ask for a corrected address. Do not "
                "retry create_shipment until it is fixed.",
                suggestions=[s.model_dump(exclude_none=True) for s in check.suggestions],
            )
        if check.normalized:
            # Commit exactly what the user is shown: the carrier-normalised address.
            req = req.model_copy(
                update={
                    "recipient": check.normalized.model_copy(update={"email": req.recipient.email})
                }
            )
        dest = req.recipient
        rates = ctx.carrier.get_rates(
            RateRequest(
                origin=Location(city=req.shipper.city, country=req.shipper.country),
                destination=Location(city=dest.city, country=dest.country),
                weight_kg=req.package.weight_kg,
                dimensions=req.package.dimensions,
            )
        )
    except CarrierError as exc:
        return None, _carrier_err(exc)
    quote = next((q for q in rates.quotes if q.service_code == req.service_code), None)
    if quote is None:
        return None, error_result(
            "service_unavailable",
            f"Service '{req.service_code}' is not available for this shipment.",
            "Tell the user which services are available and ask which to use.",
            available=[q.service_code for q in rates.quotes],
        )
    summary = {
        "shipper": req.shipper.model_dump(exclude_none=True),
        "recipient": req.recipient.model_dump(exclude_none=True),
        "package": req.package.model_dump(mode="json", exclude_none=True),
        "service_code": quote.service_code,
        "service_name": quote.service_name,
        "price": quote.price,
        "currency": quote.currency,
        "transit_days": f"{quote.transit_days_min}-{quote.transit_days_max}",
        "estimated_delivery": quote.estimated_delivery.isoformat(),
        "reference": req.reference,
    }
    return req, summary


def commit_shipment(req: ShipmentRequest, ctx: ToolContext) -> dict:
    """Actually create the shipment. Only the graph's confirm node may call this."""
    try:
        conf = ctx.carrier.create_shipment(req)
    except CarrierError as exc:
        return _carrier_err(exc) | {"created": False}
    return {"ok": True, "created": True, **conf.model_dump(mode="json", exclude_none=True)}

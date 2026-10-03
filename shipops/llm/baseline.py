"""RuleBasedLLM: a deterministic, keyword/regex policy that speaks the LLM tool-calling
protocol. It is NOT a language model.

Purpose:
* run the whole stack with no API key (LLM_PROVIDER=offline) for smoke tests and demos;
* serve as the *baseline* row in the eval tables, so LLM results have a reference point.

Policy per user turn: (1) map the message to tool calls with regexes; (2) once tool
results arrive, render a templated answer from them. It never states facts that are not
in a tool result.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from shipops.geo import CITY_COUNTRY
from shipops.llm.base import LLMResult

_TN = re.compile(r"\bMC\d{9}\b", re.I)
_ORDER = re.compile(r"(?:order|ord|#)\s*(?:no\.?|number)?\s*#?-?\s*(\d{4,6})\b", re.I)
_BARE_NUM = re.compile(r"(?<![\d.])(\d{4,5})(?![\d.]|\s*(?:kg|g|cm|km|usd|\$))", re.I)
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilos?|g|grams?|lb|lbs)\b", re.I)
_DIMS = re.compile(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)")
_DEADLINE = re.compile(r"(?:within|in|under|less than)\s+(\d+)\s+(?:business\s+)?days?", re.I)
_DOC = re.compile(r"\bdoc[_-][a-z0-9]{4,}\b", re.I)
_SERVICE = re.compile(r"\b(express|standard|economy|freight)\b", re.I)

_KW = {
    "email": re.compile(
        r"\b(e-?mail|draft|notify|notification|write to|message (the )?"
        r"customer|let (the )?customer know|inform)\b",
        re.I,
    ),
    "create": re.compile(
        r"\b(create|book|ship it|ship order|ship #?\d|generate (a )?label|"
        r"buy (a )?label|dispatch|send order)\b",
        re.I,
    ),
    "rates": re.compile(
        r"\b(rates?|quotes?|cost|price|pricing|cheapest|fastest|compare|"
        r"how much|options)\b",
        re.I,
    ),
    "address": re.compile(
        r"\b(validate|verify|check)\b.*\baddress\b|\baddress\b.*\b(valid|ok|"
        r"correct|right)\b",
        re.I,
    ),
    "document": re.compile(r"\b(invoice|bill of lading|bol|document|docs?|uploaded|pdf)\b", re.I),
    "order_info": re.compile(
        r"\b(order details|details (of|for)|what('s| is) in order|"
        r"look ?up order)\b",
        re.I,
    ),
}


def _cities(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for city in sorted(CITY_COUNTRY, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(city)}\b", low):
            if not any(m.start() >= s and m.end() <= e for _, s, e in hits):
                hits.append((city, m.start(), m.end()))
    return [c.title() for c, _, _ in sorted(hits, key=lambda h: h[1])]


def _order_id(text: str) -> str | None:
    m = _ORDER.search(text)
    if m:
        return m.group(1)
    m = _BARE_NUM.search(_TN.sub(" ", text))
    return m.group(1) if m else None


def _kg(text: str) -> float | None:
    m = _WEIGHT.search(text)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("g"):
        return val / 1000
    if unit.startswith("lb"):
        return round(val * 0.4536, 2)
    return val


def plan(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Map one user message to tool calls."""
    tn = (_TN.search(text) or [None])[0]
    tn = tn.upper() if tn else None
    oid = _order_id(text)
    ref = {"tracking_number": tn} if tn else ({"order_id": oid} if oid else {})

    docs = _DOC.findall(text)
    if docs:
        return [("analyze_documents", {"document_ids": docs})]

    if _KW["email"].search(text) and ref:
        low = text.lower()
        kind = (
            "delay"
            if re.search(r"delay|late|weather", low)
            else "customs_hold"
            if "customs" in low
            else "delivery_failed"
            if re.search(r"failed|address|missed", low)
            else "auto"
        )
        return [("draft_customer_email", {**ref, "email_type": kind})]

    if _KW["create"].search(text) and oid and not tn:
        svc = _SERVICE.search(text)
        if svc:
            return [("create_shipment", {"order_id": oid, "service_code": svc.group(1).upper()})]
        return [("get_order", {"order_id": oid})]

    if _KW["address"].search(text) and oid:
        return [("validate_address", {"order_id": oid})]

    cities = _cities(text)
    weight = _kg(text)
    if _KW["rates"].search(text) and (cities or weight):
        if len(cities) >= 2:
            origin, dest = cities[0], cities[1]
        else:
            origin, dest = "Kolkata", (cities[0] if cities else "")
        args: dict[str, Any] = {
            "origin_city": origin,
            "destination_city": dest,
            "weight_kg": weight or 1.0,
        }
        dims = _DIMS.search(text)
        if dims:
            args |= {
                "length_cm": float(dims.group(1)),
                "width_cm": float(dims.group(2)),
                "height_cm": float(dims.group(3)),
            }
        low = text.lower()
        if re.search(r"cheap|budget|lowest|save money|economical", low):
            args["priority"] = "cheapest"
        elif re.search(r"fast|urgent|asap|quick|express", low):
            args["priority"] = "fastest"
        dl = _DEADLINE.search(text)
        if dl:
            args["deadline_days"] = int(dl.group(1))
        return [("compare_rates", args)]

    if _KW["order_info"].search(text) and oid:
        return [("get_order", {"order_id": oid})]

    if ref:
        return [("track_shipment", ref)]
    return []


# ------------------------------------------------------------------------------------
# Answer templates
# ------------------------------------------------------------------------------------


def _fmt_track(r: dict) -> str:
    if not r.get("ok"):
        if r.get("error") in ("carrier_timeout", "carrier_unavailable", "carrier_error"):
            return (
                f"I couldn't get tracking for {r.get('tracking_number', 'that shipment')} "
                "right now - the carrier system didn't respond. I won't guess the status; "
                "please try again in a few minutes."
            )
        return f"I couldn't find that shipment: {r.get('message')}"
    s = r["status"].replace("_", " ")
    lines = [
        f"**{r['tracking_number']}** ({r['origin']} → {r['destination']}) is "
        f"**{s}**: {r['status_detail']}.",
        f"Current location: {r['current_location']}.",
    ]
    if r.get("delivered_at"):
        lines.append(f"Delivered at {r['delivered_at']}.")
    elif r.get("estimated_delivery"):
        eta = f"Estimated delivery: {r['estimated_delivery']}"
        if r.get("original_estimated_delivery"):
            eta += f" (originally {r['original_estimated_delivery']})"
        lines.append(eta + ".")
    if r.get("exception"):
        e = r["exception"]
        lines.append(f"Issue: {e['reason']} Next step: {e['action_required']}")
    return " ".join(lines)


def _fmt_rates(r: dict) -> str:
    if not r.get("ok"):
        return f"I couldn't get rates: {r.get('message')}"
    rows = [
        f"- {q['service']} ({q['service_code']}): {q['currency']} {q['price']:.2f}, "
        f"{q['transit_days']} days"
        for q in r["quotes"]
    ]
    rec = r["recommendation"]
    return (
        f"Quotes for {r['route']} ({r['chargeable_weight_kg']} kg chargeable):\n"
        + "\n".join(rows)
        + f"\n\nRecommended: **{rec['service_code']}** - "
        + "; ".join(rec["reasons"])
    )


def _fmt_email(r: dict) -> str:
    if not r.get("ok"):
        return (
            "I can't draft that email right now because the tracking data is unavailable "
            f"({r.get('message')}). I won't send the customer unverified details."
        )
    d = r["draft"]
    return f"Here's a draft for {d['to']}:\n\n**Subject:** {d['subject']}\n\n{d['body']}"


def _fmt_create(r: dict) -> str:
    if r.get("created"):
        return (
            f"Shipment created. Tracking number **{r['tracking_number']}**, "
            f"{r['service_code']} at {r['currency']} {r['price']:.2f}, estimated delivery "
            f"{r['estimated_delivery']}. Label: {r['label_url']}"
        )
    if r.get("error") == "user_declined":
        return "Understood - the shipment was not created."
    return f"I couldn't create the shipment: {r.get('message')}"


def _fmt_order(r: dict) -> str:
    if not r.get("ok"):
        return r.get("message", "Order not found.")
    d = r["destination"]
    out = (
        f"Order #{r['order_id']} for {r['customer']['name']} ({d['city']}, {d['country']}): "
        f"{', '.join(r['items'])}, value {r['currency']} {r['value']:.2f}. "
        f"Status: {r['status'].replace('_', ' ')}."
    )
    if r.get("tracking_number"):
        out += f" Tracking: {r['tracking_number']}."
    else:
        out += " Which service should I use (Express, Standard or Economy)?"
    return out


def _fmt_address(r: dict) -> str:
    if not r.get("ok"):
        return r.get("message", "Could not validate the address.")
    if r["valid"]:
        return "The address is valid."
    return "The address has problems: " + "; ".join(r["issues"]) + "."


def _fmt_docs(r: dict) -> str:
    if not r.get("ok"):
        return f"I couldn't analyse the documents: {r.get('message')}"
    parts = []
    for d in r["documents"]:
        issues = d.get("issues") or []
        parts.append(
            f"- {d['document_id']} ({d.get('doc_type')}): "
            + ("no issues" if not issues else "; ".join(i["message"] for i in issues))
        )
    for i in r.get("cross_document_issues") or []:
        parts.append(f"- Cross-check: {i['message']}")
    return "Document check:\n" + "\n".join(parts)


_FMT = {
    "track_shipment": _fmt_track,
    "compare_rates": _fmt_rates,
    "draft_customer_email": _fmt_email,
    "create_shipment": _fmt_create,
    "get_order": _fmt_order,
    "validate_address": _fmt_address,
    "analyze_documents": _fmt_docs,
}

HELP = (
    "I can track shipments (e.g. 'Where is order 4512?'), compare rates ('rates from "
    "Mumbai to Lagos for 5 kg'), create shipments ('ship order 4550 with standard'), "
    "check documents, and draft customer emails ('draft a delay email for order 4521')."
)


class RuleBasedLLM:
    provider = "offline"
    model = "rule-based-baseline"

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        after = messages[last_user + 1 :]
        if not any(m["role"] == "tool" for m in after):
            calls = plan(messages[last_user]["content"] or "")
            if calls:
                msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{uuid.uuid4().hex[:10]}",
                            "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)},
                        }
                        for n, a in calls
                    ],
                }
            else:
                msg = {"role": "assistant", "content": HELP}
            return LLMResult(message=msg, provider=self.provider, model=self.model)

        names = {c["id"]: c["function"]["name"] for m in after for c in m.get("tool_calls") or []}
        parts = []
        for m in after:
            if m["role"] == "tool":
                name = names.get(m["tool_call_id"], "")
                result = json.loads(m["content"])
                fmt = _FMT.get(name)
                parts.append(fmt(result) if fmt else json.dumps(result))
        return LLMResult(
            message={"role": "assistant", "content": "\n\n".join(parts)},
            provider=self.provider,
            model=self.model,
        )

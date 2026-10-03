"""Template-based customer email drafts, grounded strictly in tracking data.

Templates (not free-form LLM text) guarantee the facts in the email — dates, statuses,
reasons — come from the carrier, never from the model's imagination.
"""

from __future__ import annotations

from datetime import datetime

from shipops.carriers.models import TrackingInfo
from shipops.orders import Order, business_address

EMAIL_TYPES = ("auto", "delay", "customs_hold", "delivery_failed", "delivered", "in_transit")

_STATUS_TO_TYPE = {
    "delayed": "delay",
    "customs_hold": "customs_hold",
    "delivery_failed": "delivery_failed",
    "delivered": "delivered",
}


def _d(dt: datetime | None) -> str:
    return dt.strftime("%A, %d %B %Y").replace(" 0", " ") if dt else "to be confirmed"


def draft(
    info: TrackingInfo, order: Order | None, email_type: str = "auto", note: str | None = None
) -> dict:
    kind = (
        email_type
        if email_type in EMAIL_TYPES and email_type != "auto"
        else (_STATUS_TO_TYPE.get(info.status, "in_transit"))
    )
    biz = business_address()
    first = order.customer.name.split()[0] if order else "there"
    ref = f"order #{order.order_id}" if order else f"shipment {info.tracking_number}"
    exc = info.exception
    sign = f"\n\nWarm regards,\n{biz.name}\n{biz.company}\n{biz.email}"
    track_line = f"Tracking number: {info.tracking_number}"

    if kind == "delay":
        subject = f"Update on your {ref}: new delivery date"
        reason = exc.reason if exc else info.status_detail
        body = (
            f"Hi {first},\n\nWe wanted to let you know that your {ref} has been delayed. "
            f"Reason given by the carrier: {reason}\n\n"
            f"Original estimated delivery: {_d(info.original_estimated_delivery)}\n"
            f"Revised estimated delivery: {_d(info.estimated_delivery)}\n{track_line}\n\n"
            "We're sorry for the wait and are keeping a close eye on it. No action is needed "
            "from you."
        )
    elif kind == "customs_hold":
        subject = f"Your {ref} is being held at customs"
        body = (
            f"Hi {first},\n\nYour {ref} has reached {info.destination} but is currently held "
            f"by customs. {exc.reason if exc else info.status_detail}\n\n"
            "We are preparing the required documents with the carrier now and will update "
            "you as soon as it is released. If customs asks you directly for an importer "
            f"ID/TIN, please reply to this email and we'll help.\n\n{track_line}"
        )
    elif kind == "delivery_failed":
        subject = f"Action needed: delivery attempt for your {ref}"
        body = (
            f"Hi {first},\n\nThe courier tried to deliver your {ref} but couldn't complete "
            f"the delivery. {exc.reason if exc else info.status_detail}\n\n"
            "Could you reply with your full delivery address (including building number "
            "or a nearby landmark) and a phone number the courier can reach? "
            f"{exc.action_required if exc else ''}\n\n{track_line}"
        )
    elif kind == "delivered":
        subject = f"Your {ref} has been delivered"
        body = (
            f"Hi {first},\n\nGood news - the carrier reports your {ref} was delivered on "
            f"{_d(info.delivered_at)}. ({info.status_detail})\n\nIf anything is missing or "
            "damaged, just reply to this email.\n\n" + track_line
        )
    else:
        subject = f"Your {ref} is on its way"
        body = (
            f"Hi {first},\n\nYour {ref} is in transit. Latest update: {info.status_detail} "
            f"({info.current_location}).\n\nEstimated delivery: "
            f"{_d(info.estimated_delivery)}\n{track_line}"
        )
    if note:
        body += f"\n\n{note.strip()}"
    return {
        "to": order.customer.email if order else None,
        "subject": subject,
        "body": body + sign,
        "email_type": kind,
        "grounded_on": {"tracking_number": info.tracking_number, "status": info.status},
    }

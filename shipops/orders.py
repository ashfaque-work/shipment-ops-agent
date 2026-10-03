"""Read-only access to the business's order book (JSON fixture; swap for a DB later)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from shipops.carriers.models import Address

DATA = Path(__file__).parent / "data" / "orders.json"


class Customer(BaseModel):
    name: str
    email: str
    phone: str


class OrderPackage(BaseModel):
    weight_kg: float
    length_cm: float
    width_cm: float
    height_cm: float
    description: str


class Order(BaseModel):
    order_id: str
    status: str
    tracking_number: str | None
    customer: Customer
    destination: Address
    items: list[str]
    value: float
    currency: str
    package: OrderPackage | None = None

    def recipient_address(self) -> Address:
        return self.destination.model_copy(
            update={
                "name": self.customer.name,
                "phone": self.customer.phone,
                "email": self.customer.email,
            }
        )


@lru_cache
def _load() -> tuple[Address, dict[str, Order]]:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    orders = {o["order_id"]: Order.model_validate(o) for o in raw["orders"]}
    return Address.model_validate(raw["business"]), orders


def business_address() -> Address:
    return _load()[0]


def normalize_order_id(order_id: str | int) -> str:
    """'#4512', 'ORD-4512', 'order 4512' -> '4512'."""
    digits = re.findall(r"\d+", str(order_id))
    return digits[-1] if digits else str(order_id).strip()


def get_order(order_id: str | int) -> Order | None:
    return _load()[1].get(normalize_order_id(order_id))


def find_order_by_tracking(tracking_number: str) -> Order | None:
    tn = tracking_number.strip().upper()
    return next((o for o in _load()[1].values() if o.tracking_number == tn), None)


def all_orders() -> list[Order]:
    return list(_load()[1].values())

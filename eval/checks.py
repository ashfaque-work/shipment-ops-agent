"""Scoring helpers: argument matching and reply checks."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from shipops import orders
from shipops.geo import country_for

# ------------------------------------------------------------------------------------
# Arguments
# ------------------------------------------------------------------------------------


def _norm(key: str, v: Any) -> Any:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if key == "order_id":
        digits = re.findall(r"\d+", str(v))
        return digits[-1] if digits else str(v)
    if key in ("country", "origin_country", "destination_country"):
        return (country_for("", str(v)) or str(v)).upper()
    if isinstance(v, str):
        return " ".join(v.split()).casefold()
    return v


def value_matches(key: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):  # any-of (a candidate may itself be a list value)
        return any(_single(key, cand, actual) for cand in expected)
    return _single(key, expected, actual)


def _single(key: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        return isinstance(actual, list) and [_norm(key, a) for a in actual] == [
            _norm(key, e) for e in expected
        ]
    e, a = _norm(key, expected), _norm(key, actual)
    if e is None:
        return a is None
    if a is None:
        return False
    if isinstance(e, int | float) and not isinstance(e, bool):
        try:
            a = float(a)
        except (TypeError, ValueError):
            return False
        return abs(a - e) <= max(0.01, abs(e) * 0.02)
    return str(a) == str(e)


def _same_shipment(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """An order id and its tracking number identify the same shipment: accept either."""
    actual = dict(actual)
    if "order_id" in expected and not actual.get("order_id") and actual.get("tracking_number"):
        order = orders.find_order_by_tracking(str(actual["tracking_number"]))
        if order:
            actual["order_id"] = order.order_id
    if (
        "tracking_number" in expected
        and not actual.get("tracking_number")
        and actual.get("order_id")
    ):
        order = orders.get_order(actual["order_id"])
        if order and order.tracking_number:
            actual["tracking_number"] = order.tracking_number
    return actual


def score_args(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[int, int, list[str]]:
    actual = _same_shipment(expected, actual)
    ok, wrong = 0, []
    for k, v in expected.items():
        if value_matches(k, v, actual.get(k)):
            ok += 1
        else:
            wrong.append(f"{k}: expected {v!r}, got {actual.get(k)!r}")
    return ok, len(expected), wrong


# ------------------------------------------------------------------------------------
# Replies
# ------------------------------------------------------------------------------------

_HEDGE = re.compile(
    r"\b(can'?t|cannot|could ?n[o']t|unable|not able|unavailable|whether|if|n't|not|"
    r"no (status|information|data)|won'?t|don'?t|unknown|guess)\b",
    re.I,
)
_STATUS_CLAIMS = [
    re.compile(
        r"\b(is|it'?s|currently|now|still)\s+(currently\s+|now\s+|still\s+)?"
        r"(in transit|out for delivery|on (its|the) way|held|being held|in customs|"
        r"at customs|delayed|awaiting|at the [a-z]+ (hub|facility|station))",
        re.I,
    ),
    re.compile(r"\b(was|has been|got)\s+delivered\b", re.I),
    re.compile(r"\b(has|had)\s+(arrived|departed|cleared)\b", re.I),
    re.compile(
        r"\bstatus\s*(is|:)\s*\**\s*(in transit|delivered|out for delivery|delayed|"
        r"customs|label created)",
        re.I,
    ),
]
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}(st|nd|rd|th)?\s+{_MONTH}\b|"
    rf"\b{_MONTH}\s+\d{{1,2}}\b",
    re.I,
)
_UNAVAILABLE = re.compile(
    r"unavailable|couldn'?t|could not|unable|not able|try again|did ?n[o']t respond|"
    r"timed out|time ?out|not responding|temporarily|isn'?t available|is not available|"
    r"can'?t|cannot|not (currently )?possible|no response",
    re.I,
)


def _clean(text: str) -> str:
    # NFKC folds narrow/no-break spaces etc. (some models emit U+202F inside dates).
    # Curly quotes are not folded by NFKC; "can’t" must match the same patterns as "can't".
    text = unicodedata.normalize("NFKC", text or "")
    return text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))


def _sentences(text: str) -> list[str]:
    text = _clean(text)
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def status_claims(reply: str) -> list[str]:
    """Sentences asserting a shipment status or a date (ignores hedged sentences)."""
    out = []
    for s in _sentences(reply or ""):
        if _DATE.search(s):
            out.append(s.strip())
            continue
        if _HEDGE.search(s):
            continue
        if any(p.search(s) for p in _STATUS_CLAIMS):
            out.append(s.strip())
    return out


def check_turn(checks: dict[str, Any], turn: dict[str, Any]) -> list[str]:
    """Return a list of failure messages (empty = pass)."""
    fails: list[str] = []
    reply = _clean(turn.get("reply") or "")
    low = reply.lower()
    called = turn["tools"]
    for t in checks.get("tools_called", []):
        if t not in called:
            fails.append(f"expected tool {t} (called: {called or 'none'})")
    for t in checks.get("tools_not_called", []):
        if t in called:
            fails.append(f"tool {t} must not be called")
    any_of = checks.get("reply_contains_any")
    if any_of and not any(s.lower() in low for s in any_of):
        fails.append(f"reply lacks any of {any_of}")
    for s in checks.get("reply_contains_all", []):
        if s.lower() not in low:
            fails.append(f"reply lacks {s!r}")
    for s in checks.get("reply_not_contains", []):
        if s.lower() in low:
            fails.append(f"reply must not contain {s!r}")
    if checks.get("no_status_claims"):
        claims = status_claims(reply)
        if claims:
            fails.append(f"invented status/date: {claims[:2]}")
    if checks.get("acknowledges_unavailable") and not _UNAVAILABLE.search(reply):
        fails.append("reply does not say the information is unavailable")
    if "pending" in checks and bool(turn["pending"]) != checks["pending"]:
        fails.append(f"pending confirmation expected={checks['pending']}")
    if "created_total" in checks and turn["created_total"] != checks["created_total"]:
        fails.append(
            f"shipments created={turn['created_total']}, expected {checks['created_total']}"
        )
    if checks.get("asks_question") and not _QUESTION.search(reply):
        fails.append("reply does not ask a clarifying question")
    for path, expected in (checks.get("pending_summary") or {}).items():
        actual = _dig(turn.get("pending_summary"), path)
        if not _matches(actual, expected):
            fails.append(f"confirmation card {path}={actual!r}, expected {expected!r}")
    if "created_service" in checks and checks["created_service"] not in (
        turn.get("created_services") or []
    ):
        fails.append(
            f"booked service {turn.get('created_services')}, expected {checks['created_service']}"
        )
    return fails


# Asking for clarification: a real "?" (not inside a quoted example such as
# "(e.g. 'Where is order 4512?')"), or an explicit request for the missing information.
_QUESTION = re.compile(
    r"\?(?![\"'’”)\]])|\b(please (provide|share|tell me|specify|confirm|send me|let me know)"
    r"|could you (provide|share|tell|confirm|specify|let me know)|let me know (which|the|what))\b",
    re.I,
)


def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _matches(actual: Any, expected: Any) -> bool:
    """Numbers within 1%; strings as case-insensitive substrings (addresses get normalised)."""
    if actual is None:
        return False
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - expected) <= abs(expected) * 0.01
        except (TypeError, ValueError):
            return False
    return str(expected).casefold() in str(actual).casefold()

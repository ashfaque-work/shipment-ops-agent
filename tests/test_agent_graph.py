from __future__ import annotations

import json

from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.llm.base import LLMError, LLMResult
from shipops.llm.scripted import ScriptedLLM, call, calls, say


def _created(ctx: ToolContext) -> int:
    return ctx.carrier.calls.count(("POST", "/shipments"))


def _last_tool_result(messages: list[dict]) -> dict:
    return json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])


def test_simple_tracking_turn(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            call("track_shipment", order_id="4512"),
            lambda msgs: say(f"Status: {_last_tool_result(msgs)['status']}"),
        ]
    )
    svc = AgentService(llm, ctx)
    out = svc.chat("s1", "Where is order 4512 going to Lagos?")
    assert out.reply == "Status: in_transit"
    assert out.pending_confirmation is None
    tools = [t for t in out.trace if t["type"] == "tool"]
    assert [t["name"] for t in tools] == ["track_shipment"] and tools[0]["ok"]
    # system prompt present and first
    assert llm.requests[0][0]["role"] == "system"


def test_create_shipment_waits_for_confirmation_then_creates(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            call("create_shipment", order_id="4550", service_code="STANDARD"),
            lambda msgs: say(f"Booked: {_last_tool_result(msgs)['tracking_number']}"),
        ]
    )
    svc = AgentService(llm, ctx)
    out = svc.chat("s", "Ship order 4550 with standard")
    assert out.reply is None
    p = out.pending_confirmation
    assert p and p["type"] == "confirm_shipment"
    assert p["summary"]["recipient"]["name"] == "Njeri Mwangi" and p["summary"]["price"] > 0
    assert _created(ctx) == 0, "nothing created before confirmation"
    assert svc.pending("s") is not None

    done = svc.confirm("s", True)
    assert _created(ctx) == 1
    assert done.reply and done.reply.startswith("Booked: MC2")
    entry = next(t for t in done.trace if t["name"] == "create_shipment")
    assert entry["status"] == "approved" and entry["result"]["created"] is True
    assert svc.pending("s") is None


def test_decline_does_not_create(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            call("create_shipment", order_id="4550", service_code="EXPRESS"),
            lambda msgs: say(
                "OK, cancelled." if _last_tool_result(msgs)["error"] == "user_declined" else "??"
            ),
        ]
    )
    svc = AgentService(llm, ctx)
    svc.chat("s", "book 4550 express")
    out = svc.confirm("s", False)
    assert out.reply == "OK, cancelled."
    assert _created(ctx) == 0


def test_typed_yes_confirms(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [call("create_shipment", order_id="4553", service_code="ECONOMY"), say("Done")]
    )
    svc = AgentService(llm, ctx)
    svc.chat("s", "ship 4553 economy")
    assert svc.chat("s", "Yes, go ahead!").reply == "Done"
    assert _created(ctx) == 1


def test_ambiguous_reply_cancels_and_is_handled_as_new_request(ctx: ToolContext) -> None:
    def respond(msgs):
        assert msgs[-1] == {"role": "user", "content": "actually what does express cost?"}
        assert _last_tool_result(msgs)["error"] == "user_declined"
        return say("Not booked. Let me check express pricing.")

    llm = ScriptedLLM([call("create_shipment", order_id="4553", service_code="ECONOMY"), respond])
    svc = AgentService(llm, ctx)
    svc.chat("s", "ship 4553 economy")
    out = svc.chat("s", "actually what does express cost?")
    assert out.reply.startswith("Not booked")
    assert _created(ctx) == 0


def test_preflight_failure_skips_confirmation(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            call("create_shipment", order_id="4551", service_code="STANDARD"),
            lambda msgs: say(_last_tool_result(msgs)["error"]),
        ]
    )
    svc = AgentService(llm, ctx)
    out = svc.chat("s", "ship order 4551")
    assert out.pending_confirmation is None
    assert out.reply == "invalid_address"
    assert _created(ctx) == 0


def test_two_create_calls_in_one_message_only_one_pending(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            calls(
                ("create_shipment", {"order_id": "4550", "service_code": "STANDARD"}),
                ("create_shipment", {"order_id": "4553", "service_code": "STANDARD"}),
            ),
            say("one at a time"),
        ]
    )
    svc = AgentService(llm, ctx)
    out = svc.chat("s", "ship 4550 and 4553")
    assert out.pending_confirmation["summary"]["recipient"]["name"] == "Njeri Mwangi"
    out = svc.confirm("s", True)
    assert _created(ctx) == 1
    msgs = svc.history("s")
    # Every tool call got exactly one tool response (valid OpenAI transcript).
    call_ids = [c["id"] for m in msgs for c in m.get("tool_calls") or []]
    resp_ids = [m["tool_call_id"] for m in msgs if m["role"] == "tool"]
    assert sorted(call_ids) == sorted(resp_ids)


def test_confirm_without_pending(ctx: ToolContext) -> None:
    svc = AgentService(ScriptedLLM([]), ctx)
    assert "no shipment" in svc.confirm("s", True).reply.lower()


def test_parallel_safe_tools(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            calls(
                ("track_shipment", {"order_id": "4518"}),
                ("draft_customer_email", {"order_id": "4518"}),
            ),
            say("done"),
        ]
    )
    out = AgentService(llm, ctx).chat("s", "customs hold for 4518? draft email")
    assert [t["name"] for t in out.trace if t["type"] == "tool"] == [
        "track_shipment",
        "draft_customer_email",
    ]


def test_step_limit_guard(ctx: ToolContext) -> None:
    llm = ScriptedLLM([call("get_order", order_id="4550")] * 20)
    out = AgentService(llm, ctx).chat("s", "loop forever")
    assert "reasonable number of steps" in out.reply


def test_llm_outage_degrades_gracefully(ctx: ToolContext) -> None:
    class Down:
        def complete(self, messages, tools) -> LLMResult:
            raise LLMError("all providers 429")

    out = AgentService(Down(), ctx).chat("s", "hi")
    assert "temporarily unavailable" in out.reply
    assert out.trace[0]["ok"] is False


def test_sessions_are_isolated(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [call("create_shipment", order_id="4550", service_code="STANDARD"), say("hello")]
    )
    svc = AgentService(llm, ctx)
    svc.chat("a", "ship 4550")
    assert svc.pending("a") and svc.pending("b") is None
    assert svc.chat("b", "hi").reply == "hello"
    assert svc.pending("a") is not None


def test_concurrent_approvals_run_one_turn(ctx: ToolContext) -> None:
    """A double-clicked Book button: the second request must not resume the same step."""
    import threading

    llm = ScriptedLLM(
        [
            call("create_shipment", order_id="4550", service_code="STANDARD"),
            say("Booked."),
            say("unexpected second turn"),
        ]
    )
    svc = AgentService(llm, ctx)
    svc.chat("s", "Ship order 4550 with standard")
    replies: list[str | None] = []
    threads = [
        threading.Thread(target=lambda: replies.append(svc.confirm("s", True))) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.reply or "" for r in replies) == [
        "Booked.",
        "There is no shipment waiting for confirmation.",
    ]
    assert _created(ctx) == 1
    tool_results = [m for m in svc.history("s") if m["role"] == "tool"]
    assert len(tool_results) == 1  # no duplicate result for the same tool call


def test_booked_order_cannot_be_booked_again(ctx: ToolContext) -> None:
    llm = ScriptedLLM(
        [
            call("create_shipment", order_id="4550", service_code="STANDARD"),
            say("Booked."),
            call("create_shipment", order_id="4550", service_code="EXPRESS"),
            lambda msgs: say(_last_tool_result(msgs)["error"]),
            call("track_shipment", order_id="4550"),
            lambda msgs: say(_last_tool_result(msgs)["status"]),
        ]
    )
    svc = AgentService(llm, ctx)
    svc.chat("s", "Ship order 4550 with standard")
    tn = next(t["result"]["tracking_number"] for t in svc.confirm("s", True).trace if t.get("ok"))
    out = svc.chat("s", "Ship order 4550 again with express")
    assert out.pending_confirmation is None and out.reply == "already_shipped"
    assert _created(ctx) == 1
    # Tracking the order follows the new label.
    out = svc.chat("s", "Where is order 4550?")
    assert out.reply == "label_created"
    assert out.trace[-2]["result"]["tracking_number"] == tn


def test_booking_in_one_conversation_does_not_block_another(ctx: ToolContext) -> None:
    """Shared demo: one visitor booking order 4550 must not stop the next visitor."""
    book = call("create_shipment", order_id="4550", service_code="STANDARD")
    svc = AgentService(ScriptedLLM([book, say("Booked."), book]), ctx)
    svc.chat("alice", "Ship order 4550 with standard")
    svc.confirm("alice", True)
    assert svc.chat("bob", "Ship order 4550 with standard").pending_confirmation


def test_unknown_currency_is_rejected_before_confirmation(ctx: ToolContext) -> None:
    recipient = {
        "name": "Ada Obi",
        "street1": "12 Allen Avenue",
        "city": "Lagos",
        "postal_code": "100271",
        "country": "NG",
        "phone": "+2348012345678",
    }
    llm = ScriptedLLM(
        [
            call(
                "create_shipment",
                service_code="STANDARD",
                recipient=recipient,
                package={"weight_kg": 2, "description": "Scarves", "currency": "rupees"},
            ),
            lambda msgs: say(_last_tool_result(msgs)["error"]),
        ]
    )
    out = AgentService(llm, ctx).chat("s", "ship scarves to Lagos")
    assert out.pending_confirmation is None and out.reply == "invalid_arguments"


def test_idle_sessions_are_evicted(ctx: ToolContext) -> None:
    now = [0.0]
    llm = ScriptedLLM([say("one"), say("two"), say("three")])
    svc = AgentService(llm, ctx, max_sessions=2, session_ttl_s=100, clock=lambda: now[0])
    svc.chat("a", "hi")
    svc.chat("b", "hi")
    svc.chat("c", "hi")  # over the cap: the least recently used session goes
    assert svc.history("a") == [] and svc.history("b") and svc.history("c")
    now[0] = 500  # both remaining sessions are now idle past the TTL
    svc._session_lock("d")
    assert svc.history("b") == [] and svc.history("c") == []

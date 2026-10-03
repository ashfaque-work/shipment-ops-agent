"""LangGraph tool-calling agent with a human-in-the-loop gate before create_shipment.

    START -> agent --(tool calls?)--> tools --(gated call preflighted?)--> confirm -> agent
               |                        |                                  (interrupt)
               +--(final answer)--> END +--------------------------------> agent

* Every tool runs through the MCP server (see gateway.py / shipops/mcp_server.py).
* `tools` executes safe tools and, for create_shipment, only a *dry-run preview* (address
  validation + price quote + confirmation code). It never creates anything.
* `confirm` calls `interrupt()` with the deterministic preview summary, NOT model text.
  Only an explicit `{"approved": true}` resume commits - and the MCP server additionally
  elicits confirmation, which the gateway answers only for the approved code.
"""

from __future__ import annotations

import json
import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from shipops import clock
from shipops.agent import tools as T
from shipops.agent.gateway import McpGateway
from shipops.agent.prompts import system_prompt
from shipops.llm.base import LLM, LLMError

MAX_LLM_STEPS_PER_TURN = 8
MAX_HISTORY_MESSAGES = 40
MAX_TOOL_RESULT_CHARS = 6000


class AgentState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    pending: dict[str, Any] | None
    steps: int


def window(messages: list[dict[str, Any]], limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Trim history to roughly `limit` messages, always starting at a user turn so that
    tool results are never separated from the assistant message that requested them."""
    if len(messages) <= limit:
        return messages
    start = len(messages) - limit
    for i in range(start, len(messages)):
        if messages[i]["role"] == "user":
            return messages[i:]
    return messages[start:]


def _tool_msg(call_id: str, payload: dict) -> dict[str, Any]:
    content = json.dumps(payload, default=str)
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = content[:MAX_TOOL_RESULT_CHARS] + '..."[truncated]"'
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def build_graph(llm: LLM, gateway: McpGateway, checkpointer=None):
    def agent(state: AgentState) -> dict:
        steps = state.get("steps", 0) + 1
        if steps > MAX_LLM_STEPS_PER_TURN:
            msg = {
                "role": "assistant",
                "content": "I wasn't able to finish that request in "
                "a reasonable number of steps. Could you rephrase or split it up?",
            }
            return {
                "messages": [msg],
                "steps": steps,
                "trace": [{"type": "guard", "name": "step_limit", "ok": False}],
            }
        msgs = [{"role": "system", "content": system_prompt(clock.now())}]
        msgs += window(state["messages"])
        try:
            res = llm.complete(msgs, T.TOOL_SPECS)
        except LLMError as exc:
            msg = {
                "role": "assistant",
                "content": "Sorry - the AI service is temporarily "
                "unavailable (rate limit or outage). Please try again in a minute.",
            }
            return {
                "messages": [msg],
                "steps": steps,
                "trace": [{"type": "llm", "ok": False, "error": str(exc)}],
            }
        entry = {
            "type": "llm",
            "ok": True,
            "provider": res.provider,
            "model": res.model,
            "latency_ms": res.latency_ms,
            "fallback_from": res.fallback_from,
            "tool_calls": [c["function"]["name"] for c in res.tool_calls],
        }
        return {"messages": [res.message], "steps": steps, "trace": [entry]}

    def tools(state: AgentState) -> dict:
        last = state["messages"][-1]
        out: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        pending = None
        for tc in last.get("tool_calls") or []:
            name = tc["function"]["name"]
            args = T.parse_args(tc["function"].get("arguments"))
            t0 = time.perf_counter()
            if name in T.GATED_TOOLS:
                ready = False
                if pending is not None:
                    result = T.error_result(
                        "one_at_a_time",
                        "Only one shipment can be confirmed at a time. "
                        "Ask the user about the next one afterwards.",
                    )
                else:
                    preview = gateway.preview_shipment(args)
                    ready = preview.get("status") == "confirmation_required"
                    result = preview["summary"] if ready else preview
                    if ready:
                        pending = {
                            "tool_call_id": tc["id"],
                            "args": args,
                            "code": preview["confirmation_code"],
                            "summary": preview["summary"],
                        }
                ms = int((time.perf_counter() - t0) * 1000)
                status = "awaiting_confirmation" if ready else "rejected"
                trace.append(
                    {
                        "type": "tool",
                        "id": tc["id"],
                        "name": name,
                        "args": args,
                        "ok": status == "awaiting_confirmation",
                        "status": status,
                        "result": result,
                        "duration_ms": ms,
                    }
                )
                if status == "rejected":
                    out.append(_tool_msg(tc["id"], result))
                continue
            result = gateway.call(name, args)
            ms = int((time.perf_counter() - t0) * 1000)
            trace.append(
                {
                    "type": "tool",
                    "id": tc["id"],
                    "name": name,
                    "args": args,
                    "ok": bool(result.get("ok")),
                    "result": result,
                    "duration_ms": ms,
                }
            )
            out.append(_tool_msg(tc["id"], result))
        return {"messages": out, "trace": trace, "pending": pending}

    def confirm(state: AgentState) -> dict:
        p = state["pending"]
        assert p is not None
        decision = interrupt(
            {"type": "confirm_shipment", "tool_call_id": p["tool_call_id"], "summary": p["summary"]}
        )
        approved = isinstance(decision, dict) and decision.get("approved") is True
        t0 = time.perf_counter()
        if approved:
            result = gateway.commit_shipment(p["args"], p["code"])
        else:
            result = {
                "ok": False,
                "created": False,
                "error": "user_declined",
                "message": "The user did not confirm. The shipment was NOT created.",
                "guidance": "Acknowledge this. Do not call create_shipment again "
                "unless the user asks anew.",
            }
        msgs = [_tool_msg(p["tool_call_id"], result)]
        if isinstance(decision, dict) and decision.get("user_message"):
            msgs.append({"role": "user", "content": decision["user_message"]})
        trace = [
            {
                "type": "tool",
                "id": p["tool_call_id"],
                "name": "create_shipment",
                "args": p["args"],
                "ok": bool(result.get("ok")),
                "status": "approved" if approved else "declined",
                "result": result,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            }
        ]
        return {"messages": msgs, "pending": None, "trace": trace}

    def after_agent(state: AgentState) -> str:
        return "tools" if state["messages"][-1].get("tool_calls") else END

    def after_tools(state: AgentState) -> str:
        return "confirm" if state.get("pending") else "agent"

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("confirm", confirm)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, ["tools", END])
    g.add_conditional_edges("tools", after_tools, ["confirm", "agent"])
    g.add_edge("confirm", "agent")
    return g.compile(checkpointer=checkpointer)

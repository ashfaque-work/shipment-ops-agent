"""AgentService: session-aware facade over the LangGraph agent.

Used by the HTTP API, the CLI and the eval harness.
"""

from __future__ import annotations

import re
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from shipops.agent.gateway import McpGateway
from shipops.agent.graph import build_graph
from shipops.agent.tools import ToolContext
from shipops.llm.base import LLM

# A typed reply counts as explicit approval only if it is unambiguous.
_YES = re.compile(
    r"^\s*(yes|y|yep|yeah|confirm(ed)?|approve(d)?|go ahead|proceed|do it|book it|ship it|"
    r"yes,? (please )?(confirm|create|book|go ahead|proceed)( it)?)[\s.!]*$",
    re.I,
)
_NO = re.compile(r"^\s*(no|n|nope|cancel|stop|don'?t|do not|abort|decline)[\s.!]*$", re.I)


class TurnResult(BaseModel):
    session_id: str
    reply: str | None
    pending_confirmation: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)


class AgentService:
    def __init__(
        self,
        llm: LLM,
        ctx: ToolContext,
        *,
        gateway: McpGateway | None = None,
        checkpointer=None,
        recursion_limit: int = 60,
        prompt_version: int | None = None,
    ):
        from shipops.mcp_server import build_server

        self.llm = llm
        self.ctx = ctx
        # All tools run through the MCP server; in-process by default (shares ctx).
        self.gateway = gateway or McpGateway(build_server(ctx))
        self.graph = build_graph(
            llm, self.gateway, checkpointer or InMemorySaver(), prompt_version=prompt_version
        )
        self.recursion_limit = recursion_limit

    def close(self) -> None:
        self.gateway.close()

    def __enter__(self) -> AgentService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _config(self, session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": self.recursion_limit}

    def _values(self, session_id: str) -> dict[str, Any]:
        return self.graph.get_state(self._config(session_id)).values or {}

    def pending(self, session_id: str) -> dict[str, Any] | None:
        snap = self.graph.get_state(self._config(session_id))
        for intr in getattr(snap, "interrupts", ()) or ():
            if isinstance(intr.value, dict) and intr.value.get("type") == "confirm_shipment":
                return intr.value
        return None

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._values(session_id).get("messages", []))

    def _run(self, session_id: str, payload: Any) -> TurnResult:
        before = len(self._values(session_id).get("trace", []))
        self.graph.invoke(payload, self._config(session_id))
        values = self._values(session_id)
        trace = values.get("trace", [])[before:]
        pending = self.pending(session_id)
        reply = None
        if pending is None:
            last = (values.get("messages") or [{}])[-1]
            if last.get("role") == "assistant" and not last.get("tool_calls"):
                reply = last.get("content")
        return TurnResult(
            session_id=session_id, reply=reply, pending_confirmation=pending, trace=trace
        )

    def chat(self, session_id: str, text: str) -> TurnResult:
        if self.pending(session_id):
            if _YES.match(text):
                return self.confirm(session_id, True)
            if _NO.match(text):
                return self.confirm(session_id, False)
            # Anything else cancels the pending shipment and is handled as a new request.
            return self._run(session_id, Command(resume={"approved": False, "user_message": text}))
        return self._run(session_id, {"messages": [{"role": "user", "content": text}], "steps": 0})

    def confirm(self, session_id: str, approved: bool) -> TurnResult:
        if not self.pending(session_id):
            return TurnResult(
                session_id=session_id, reply="There is no shipment waiting for confirmation."
            )
        return self._run(session_id, Command(resume={"approved": bool(approved)}))

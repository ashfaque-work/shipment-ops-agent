"""AgentService: session-aware facade over the LangGraph agent.

Used by the HTTP API, the CLI and the eval harness.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
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


def _pending(snap: Any) -> dict[str, Any] | None:
    for intr in getattr(snap, "interrupts", ()) or ():
        if isinstance(intr.value, dict) and intr.value.get("type") == "confirm_shipment":
            return intr.value
    return None


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
        max_sessions: int = 500,
        session_ttl_s: float = 6 * 3600,
        clock=time.monotonic,
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
        # Conversations live in memory: bound them (LRU + idle TTL) so a long-running demo
        # can't grow without limit, and serialise turns per session so a double-clicked
        # "Book" (or two tabs) can't run the same conversation twice at once.
        self._checkpointer = self.graph.checkpointer
        self.max_sessions = max_sessions
        self.session_ttl_s = session_ttl_s
        self._clock = clock
        self._sessions: OrderedDict[str, tuple[threading.Lock, float]] = OrderedDict()
        self._sessions_lock = threading.Lock()

    def close(self) -> None:
        self.gateway.close()

    def __enter__(self) -> AgentService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _session_lock(self, session_id: str) -> threading.Lock:
        """This session's turn lock; also evicts the oldest idle sessions (LRU order)."""
        now = self._clock()
        with self._sessions_lock:
            lock = self._sessions.pop(session_id, (threading.Lock(), now))[0]
            self._sessions[session_id] = (lock, now)  # most recent last
            for sid, (old_lock, seen) in list(self._sessions.items()):
                over = len(self._sessions) > self.max_sessions
                if sid == session_id or not (over or now - seen > self.session_ttl_s):
                    break  # the rest are newer
                if old_lock.locked():
                    continue  # a turn is running in it right now
                del self._sessions[sid]
                self._checkpointer.delete_thread(sid)
        return lock

    def _config(self, session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": self.recursion_limit}

    def _values(self, session_id: str) -> dict[str, Any]:
        return self.graph.get_state(self._config(session_id)).values or {}

    def pending(self, session_id: str) -> dict[str, Any] | None:
        return _pending(self.graph.get_state(self._config(session_id)))

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._values(session_id).get("messages", []))

    def _run(self, session_id: str, payload: Any) -> TurnResult:
        config = self._config(session_id)
        before = len(self._values(session_id).get("trace", []))
        self.graph.invoke(payload, config)
        snap = self.graph.get_state(config)
        values = snap.values or {}
        trace = values.get("trace", [])[before:]
        pending = _pending(snap)
        reply = None
        if pending is None:
            last = (values.get("messages") or [{}])[-1]
            if last.get("role") == "assistant" and not last.get("tool_calls"):
                reply = last.get("content")
        return TurnResult(
            session_id=session_id, reply=reply, pending_confirmation=pending, trace=trace
        )

    def chat(self, session_id: str, text: str) -> TurnResult:
        with self._session_lock(session_id):
            return self._chat(session_id, text)

    def confirm(self, session_id: str, approved: bool) -> TurnResult:
        with self._session_lock(session_id):
            return self._confirm(session_id, approved)

    def _chat(self, session_id: str, text: str) -> TurnResult:
        if self.pending(session_id):
            if _YES.match(text):
                return self._confirm(session_id, True)
            if _NO.match(text):
                return self._confirm(session_id, False)
            # Anything else cancels the pending shipment and is handled as a new request.
            return self._run(session_id, Command(resume={"approved": False, "user_message": text}))
        return self._run(session_id, {"messages": [{"role": "user", "content": text}], "steps": 0})

    def _confirm(self, session_id: str, approved: bool) -> TurnResult:
        if not self.pending(session_id):
            return TurnResult(
                session_id=session_id, reply="There is no shipment waiting for confirmation."
            )
        return self._run(session_id, Command(resume={"approved": bool(approved)}))

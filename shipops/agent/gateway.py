"""McpGateway: the agent's only way to run tools - an MCP client session to the server.

The LangGraph agent is synchronous; the MCP client is async. The gateway owns an event loop
on a background thread, keeps one client session open, and exposes blocking calls.

Agent-facing tool names (what the LLM sees, unchanged so recorded evals stay valid) map to
MCP tool names. `create_shipment` is split into preview (dry run) and commit. For commit,
the server asks for confirmation through MCP elicitation; this client answers "accept"
only for a confirmation code that the graph's human-approval step registered via
`commit_shipment`, and "decline" for anything else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import ElicitResult

from shipops.mcp_server import CONFIRMATION_PREFIX

log = logging.getLogger(__name__)

# agent (LLM-facing) tool name -> MCP tool name
AGENT_TO_MCP = {
    "track_shipment": "track_shipment",
    "get_order": "get_order",
    "compare_rates": "get_rates",
    "validate_address": "validate_address",
    "draft_customer_email": "draft_customer_email",
    "analyze_documents": "extract_shipping_document",
}
_CODE = re.compile(re.escape(CONFIRMATION_PREFIX) + r"([0-9a-f]{12})")


class McpGateway:
    def __init__(self, server: MCPServer, *, timeout_s: float = 120.0):
        self._server = server
        self.timeout_s = timeout_s
        self._approved: set[str] = set()
        self._lock = threading.Lock()
        self.elicitations: list[dict[str, Any]] = []  # audit log: what was asked, the answer
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._client: Client | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, name="mcp-gateway", daemon=True)
        self._thread.start()
        if not self._ready.wait(30):
            raise RuntimeError("MCP gateway did not start")
        if self._startup_error is not None:
            raise RuntimeError(f"MCP gateway failed: {self._startup_error}")

    # -- lifecycle -------------------------------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with Client(self._server, elicitation_callback=self._on_elicit) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # surface startup failures to the constructor
            self._startup_error = exc
            self._ready.set()

    def close(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=10)

    # -- elicitation -----------------------------------------------------------------

    async def _on_elicit(self, context: Any, params: Any) -> ElicitResult:
        message = getattr(params, "message", "") or ""
        m = _CODE.search(message)
        code = m.group(1) if m else None
        with self._lock:
            approved = code is not None and code in self._approved
            if approved:
                self._approved.discard(code)  # single use
        self.elicitations.append({"code": code, "accepted": approved})
        if approved:
            return ElicitResult(action="accept", content={"confirm": True})
        log.warning("Declined an MCP confirmation request without human approval (code=%s)", code)
        return ElicitResult(action="decline")

    # -- calls -----------------------------------------------------------------------

    def _call_mcp(self, mcp_name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(mcp_name, args), self._loop
        )
        try:
            result = future.result(timeout=self.timeout_s)
        except TimeoutError:
            future.cancel()
            return {
                "ok": False,
                "error": "tool_timeout",
                "message": f"The {mcp_name} tool did not answer in time.",
            }
        except Exception as exc:  # protocol errors (e.g. missing capability) fail closed
            return {"ok": False, "error": "mcp_error", "message": str(exc)[:500]}
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"ok": False, "error": "tool_error", "message": text[:500]}
        if isinstance(result.structured_content, dict):
            return result.structured_content
        text = " ".join(getattr(c, "text", "") for c in result.content)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"ok": False, "error": "tool_error", "message": text[:500]}
        return parsed if isinstance(parsed, dict) else {"ok": True, "result": parsed}

    def call(self, agent_tool: str, args: dict[str, Any]) -> dict[str, Any]:
        mcp_name = AGENT_TO_MCP.get(agent_tool)
        if mcp_name is None:
            return {
                "ok": False,
                "error": "unknown_tool",
                "message": f"There is no tool called {agent_tool}.",
            }
        return self._call_mcp(mcp_name, args)

    def preview_shipment(self, args: dict[str, Any]) -> dict[str, Any]:
        """Dry run: priced summary + confirmation code, or the preflight error."""
        return self._call_mcp("create_shipment", {**args, "dry_run": True})

    def commit_shipment(self, args: dict[str, Any], code: str) -> dict[str, Any]:
        """Book. Call ONLY after a human approved the preview carrying `code`."""
        with self._lock:
            self._approved.add(code)
        try:
            return self._call_mcp("create_shipment", {**args, "dry_run": False})
        finally:
            with self._lock:
                self._approved.discard(code)

    def list_tools(self) -> list[str]:
        assert self._client is not None
        future = asyncio.run_coroutine_threadsafe(self._client.list_tools(), self._loop)
        return [t.name for t in future.result(timeout=self.timeout_s).tools]

"""ScriptedLLM: returns pre-programmed responses. For unit tests only."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from shipops.llm.base import LLMError, LLMResult

Step = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]


def call(name: str, call_id: str | None = None, **args: Any) -> dict[str, Any]:
    """Build an assistant message containing one tool call."""
    return calls((name, args), ids=[call_id] if call_id else None)


def calls(*specs: tuple[str, dict[str, Any]], ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": (ids[i] if ids else f"call_{name}_{i}"),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(specs)
        ],
    }


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


class ScriptedLLM:
    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.requests: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        self.requests.append(messages)
        if not self.steps:
            raise LLMError("ScriptedLLM ran out of steps")
        step = self.steps.pop(0)
        msg = step(messages) if callable(step) else step
        return LLMResult(message=msg, provider="scripted", model="scripted")

"""Released prompt versions are frozen: recorded evals replay against their exact text."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from shipops.agent.prompts import CURRENT_PROMPT_VERSION, PROMPTS, system_prompt

# Editing a released prompt would silently invalidate every recording made with it.
# Add a new version instead (and record new evals); never change these fingerprints.
FROZEN = {1: "d74f338c9d3e0912", 2: "009843da1e237d9f"}


@pytest.mark.parametrize("version,fingerprint", sorted(FROZEN.items()))
def test_released_prompts_are_frozen(version: int, fingerprint: str) -> None:
    assert hashlib.sha256(PROMPTS[version].encode()).hexdigest()[:16] == fingerprint


def test_current_version_is_released_and_used_by_default() -> None:
    assert CURRENT_PROMPT_VERSION in FROZEN
    now = datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert system_prompt(now) == system_prompt(now, CURRENT_PROMPT_VERSION)


def test_v2_adds_the_grounding_rules() -> None:
    assert "Never ask the user\n   for a fact a tool can look up" in PROMPTS[2]
    assert "never insert a date, status or reason supplied by the user" in PROMPTS[2]
    assert "Never ask the user" not in PROMPTS[1]


def test_agent_uses_requested_prompt_version(ctx) -> None:
    from shipops.agent.service import AgentService
    from shipops.llm.scripted import ScriptedLLM, say

    for version in (1, 2):
        llm = ScriptedLLM([say("hi")])
        with AgentService(llm, ctx, prompt_version=version) as svc:
            svc.chat("s", "hello")
        assert llm.requests[0][0]["content"] == system_prompt(
            datetime(2026, 10, 1, 9, tzinfo=UTC), version
        )

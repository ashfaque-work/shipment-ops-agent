from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mock_carrier.app import create_app
from shipops.agent.tools import ToolContext
from shipops.carriers.mock import MockCarrierAdapter


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_CLOCK", "2026-10-01T09:00:00+00:00")
    monkeypatch.setenv("MOCK_TIMEOUT_DELAY_S", "0.01")


@pytest.fixture()
def carrier() -> MockCarrierAdapter:
    """Adapter talking to an in-process mock carrier (no network, no sleeps)."""
    return MockCarrierAdapter(client=TestClient(create_app()), backoff_s=0)


@pytest.fixture()
def ctx(carrier: MockCarrierAdapter) -> ToolContext:
    return ToolContext(carrier=carrier)

"""App-level LLM record/replay (demo recordings, keyless demos)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.cassette import request_hash as eval_hash
from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.config import Settings
from shipops.documents import get_extractor
from shipops.llm import LLMError, get_llm
from shipops.llm.replay import RecordingLLM, ReplayLLM, request_hash
from shipops.llm.scripted import ScriptedLLM, call, say


def test_hash_is_shared_with_the_eval_harness() -> None:
    assert eval_hash is request_hash  # one implementation: recordings stay valid


def test_record_then_replay_a_conversation(ctx: ToolContext, tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    live = ScriptedLLM([call("track_shipment", order_id="4518"), say("Held at customs.")])
    with AgentService(RecordingLLM(live, path), ctx) as svc:
        recorded = svc.chat("s", "Why is order 4518 stuck?").reply
    data = json.loads(path.read_text())
    assert len(data["responses"]) == 2

    replay = ReplayLLM(path)
    with AgentService(replay, ctx) as svc:  # fresh session, same requests
        assert svc.chat("s", "Why is order 4518 stuck?").reply == recorded
    assert (replay.hits, replay.misses) == (2, 0)


def test_unrecorded_request_falls_back_to_baseline(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"meta": {}, "responses": {}}))
    replay = ReplayLLM(path)
    res = replay.complete([{"role": "user", "content": "hello"}], [])
    assert res.provider == "offline" and replay.misses == 1  # visible in the trace


def test_settings_wiring(tmp_path: Path) -> None:
    with pytest.raises(LLMError, match="LLM_REPLAY_PATH"):
        get_llm(Settings(llm_provider="replay"))
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"meta": {}, "responses": {}}))
    assert isinstance(
        get_llm(Settings(llm_provider="replay", llm_replay_path=str(path))), ReplayLLM
    )
    rec = get_llm(Settings(llm_provider="groq", groq_api_key="k", llm_record_path=str(path)))
    assert isinstance(rec, RecordingLLM)
    # replay / EXTRACTOR=rules never make live extraction calls
    for s in (
        Settings(llm_provider="replay", gemini_api_key="k"),
        Settings(llm_provider="gemini", gemini_api_key="k", extractor="rules"),
    ):
        assert get_extractor(s).mode == "rules"


def test_recorded_demo_file_is_clean_and_complete() -> None:
    demo = Path(__file__).resolve().parents[1] / "data" / "demo" / "demo_llm.json"
    data = json.loads(demo.read_text(encoding="utf-8"))
    assert data["meta"]["model"] and len(data["responses"]) >= 8
    assert all(r["provider"] == "gemini" for r in data["responses"].values())

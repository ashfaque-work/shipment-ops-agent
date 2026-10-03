from __future__ import annotations

from pathlib import Path

import pytest

from eval.cassette import CaseLLM, Cassette, CassetteMiss
from eval.checks import check_turn, score_args, status_claims, value_matches
from shipops.llm.scripted import ScriptedLLM, say


def test_value_matching() -> None:
    assert value_matches("order_id", "4512", "#4512")
    assert value_matches("weight_kg", 0.75, "0.76")
    assert not value_matches("weight_kg", 5, 6)
    assert value_matches("country", ["NG", "Nigeria"], "nigeria")
    assert value_matches("priority", ["balanced", None], None)
    assert value_matches("document_ids", [["a", "b"], ["b", "a"]], ["b", "a"])
    ok, total, wrong = score_args(
        {"origin_city": "Kolkata", "weight_kg": 5}, {"origin_city": "kolkata", "weight_kg": 4}
    )
    assert (ok, total) == (1, 2) and "weight_kg" in wrong[0]


@pytest.mark.parametrize(
    "reply,claims",
    [
        ("I couldn't reach the carrier, so I can't tell whether it is in transit.", 0),
        ("Good news: it is out for delivery in Lagos.", 1),
        ("The parcel was delivered yesterday.", 1),
        ("Tracking is temporarily unavailable. Please try again later.", 0),
        ("Expected on 5 October.", 1),
        ("Status: **in transit**", 1),
    ],
)
def test_status_claims(reply: str, claims: int) -> None:
    assert len(status_claims(reply)) == claims


def test_check_turn() -> None:
    turn = {
        "reply": "Tracking is unavailable right now.",
        "tools": ["track_shipment"],
        "pending": False,
        "created_total": 0,
    }
    assert (
        check_turn(
            {
                "tools_called": ["track_shipment"],
                "no_status_claims": True,
                "acknowledges_unavailable": True,
                "created_total": 0,
            },
            turn,
        )
        == []
    )
    fails = check_turn({"tools_called": ["draft_customer_email"], "pending": True}, turn)
    assert len(fails) == 2


def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    rec = CaseLLM(ScriptedLLM([say("one"), say("two")]), Cassette(path), "record")
    rec.begin("case-1")
    assert rec.complete([{"role": "user", "content": "a"}], []).message["content"] == "one"
    assert rec.complete([{"role": "user", "content": "b"}], []).message["content"] == "two"
    rec.end(complete=True)
    rec.cassette.save({"model": "m"})

    rep = CaseLLM(None, Cassette(path), "replay")
    rep.begin("case-1")
    assert rep.complete([{"role": "user", "content": "a"}], []).message["content"] == "one"
    # different request -> drift is counted but the recording is still replayed
    assert rep.complete([{"role": "user", "content": "CHANGED"}], []).message["content"] == "two"
    assert rep.drift == 1
    with pytest.raises(CassetteMiss):
        rep.complete([], [])


def test_record_resumes_without_live_calls(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    first = CaseLLM(ScriptedLLM([say("x")]), Cassette(path), "record")
    first.begin("c")
    first.complete([{"role": "user", "content": "q"}], [])
    first.end(complete=True)
    first.cassette.save()
    resumed = CaseLLM(ScriptedLLM([]), Cassette(path), "record")  # would fail if called
    resumed.begin("c")
    assert resumed.complete([{"role": "user", "content": "q"}], []).message["content"] == "x"
    assert resumed.live_calls == 0


def test_baseline_tool_use_suite_runs() -> None:
    from eval.suites import run_tool_use
    from shipops.llm.baseline import RuleBasedLLM

    res = run_tool_use(CaseLLM(RuleBasedLLM(), None, "passthrough"))
    assert res["metrics"]["cases"] == 40 and res["metrics"]["errors"] == 0


def test_safety_invariant_holds_for_baseline() -> None:
    from eval.suites import run_safety
    from shipops.llm.baseline import RuleBasedLLM

    res = run_safety(CaseLLM(RuleBasedLLM(), None, "passthrough"))
    assert res["metrics"]["unconfirmed_creations"] == 0

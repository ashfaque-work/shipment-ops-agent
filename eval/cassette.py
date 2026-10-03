"""Record / replay of LLM calls so evals run offline (CI) with real model behaviour.

A cassette is one JSON file per (label, suite):

    {"meta": {...}, "cases": {"<case_id>": {"complete": true,
                                            "calls": [{"request_hash": ..., "result": {...}}]}}}

Calls are keyed by case id + call index. The request hash detects *drift* (the prompt,
tools or tool outputs changed since recording); drift is reported, and `--strict` makes
it an error. Re-record with `--llm live --record` when it happens.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from shipops.llm.base import LLM, LLMError, LLMResult, QuotaExhausted
from shipops.redact import scrub

CASSETTE_DIR = Path(__file__).parent / "cassettes"


def request_hash(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    def canonical(m: dict) -> dict:
        # Round-trip through JSON so non-JSON values (e.g. dates) hash consistently.
        return json.loads(json.dumps(m, sort_keys=True, default=str))

    blob = json.dumps({"m": [canonical(m) for m in messages], "t": tools}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class CassetteMiss(LLMError):
    pass


class SuiteAborted(Exception):
    """Live provider out of quota: stop the suite; recorded cases are kept for resume."""


class Cassette:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"meta": {}, "cases": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def case(self, case_id: str) -> dict[str, Any] | None:
        return self.data["cases"].get(case_id)

    def is_complete(self, case_id: str) -> bool:
        c = self.case(case_id)
        return bool(c and c.get("complete"))

    def put(self, case_id: str, calls: list[dict[str, Any]], complete: bool) -> None:
        self.data["cases"][case_id] = {"complete": complete, "calls": calls}

    def save(self, meta: dict[str, Any] | None = None) -> None:
        if meta:
            self.data["meta"] |= meta
        self.data["cases"] = dict(sorted(self.data["cases"].items()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cassettes are committed: never write a credential, whatever a provider echoed back.
        self.path.write_text(
            json.dumps(scrub(self.data), indent=1) + "\n", encoding="utf-8", newline="\n"
        )


class CaseLLM:
    """LLM wrapper scoped to one eval case at a time.

    mode 'replay': answer from the cassette only.
    mode 'record': replay calls already recorded for the case (resume), call the live LLM
    for the rest, and store them.
    mode 'passthrough': just forward (baseline / no recording).
    """

    def __init__(self, inner: LLM | None, cassette: Cassette | None, mode: str):
        self.inner, self.cassette, self.mode = inner, cassette, mode
        self.case_id: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.idx = 0
        self.drift = 0
        self.live_calls = 0
        self.results: list[LLMResult] = []
        self.exhausted: QuotaExhausted | None = None
        self.live_at_begin = 0

    @property
    def case_live_calls(self) -> int:
        return self.live_calls - self.live_at_begin

    def begin(self, case_id: str) -> None:
        self.case_id, self.idx, self.drift = case_id, 0, 0
        self.live_at_begin = self.live_calls
        self.results = []
        prior = self.cassette.case(case_id) if self.cassette else None
        self.calls = list(prior["calls"]) if prior else []

    def end(self, *, complete: bool) -> None:
        if self.mode == "record" and self.cassette is not None and self.case_id:
            # keep only calls actually used this run (a shorter run truncates stale tails)
            self.cassette.put(self.case_id, self.calls[: self.idx], complete)
            self.cassette.save()  # after every case, so an interrupted run loses nothing

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        h = request_hash(messages, tools)
        if self.mode != "passthrough" and self.idx < len(self.calls):
            rec = self.calls[self.idx]
            if rec["request_hash"] != h:
                self.drift += 1
                if self.mode == "record":  # stale recording: re-record from here on
                    self.calls = self.calls[: self.idx]
                    return self._live(messages, tools, h)
            self.idx += 1
            res = LLMResult(**rec["result"])
            self.results.append(res)
            return res
        if self.mode == "replay":
            raise CassetteMiss(f"No recorded LLM response for {self.case_id} call #{self.idx}")
        return self._live(messages, tools, h)

    def _live(self, messages, tools, h: str) -> LLMResult:
        assert self.inner is not None
        if self.exhausted is not None:
            raise self.exhausted
        try:
            res = self.inner.complete(messages, tools)
        except QuotaExhausted as exc:
            self.exhausted = exc  # the agent swallows LLM errors; the suite checks this flag
            raise
        self.live_calls += 1
        self.results.append(res)
        if self.mode == "record":
            self.calls.append({"request_hash": h, "result": asdict(res)})
            self.idx += 1
        return res

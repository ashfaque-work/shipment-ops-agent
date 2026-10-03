"""Record / replay of LLM responses for the running app (demo recordings, keyless demos).

* RecordingLLM wraps a live LLM and stores every response under a hash of its exact
  request (messages + tools).
* ReplayLLM answers from such a file. The same conversation produces the same requests
  (fixed clock, deterministic tools, content-addressed document ids), so a recorded demo
  replays exactly. Anything never recorded falls back to the rule-based baseline, and the
  trace shows it ("offline"), never silently pretending to be the model.

`request_hash` is shared with the eval harness (eval/cassette.py); changing it would
invalidate every recording.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from shipops.llm.base import LLM, LLMResult


def request_hash(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    def canonical(m: dict) -> dict:
        # Round-trip through JSON so non-JSON values (e.g. dates) hash consistently.
        return json.loads(json.dumps(m, sort_keys=True, default=str))

    blob = json.dumps({"m": [canonical(m) for m in messages], "t": tools}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"meta": {}, "responses": {}}


class RecordingLLM:
    """Forwards to a live LLM and records each response by request hash."""

    def __init__(self, inner: LLM, path: str | Path):
        self.inner, self.path = inner, Path(path)
        self.data = _load(self.path)
        self._lock = threading.Lock()

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        res = self.inner.complete(messages, tools)
        with self._lock:
            self.data["responses"][request_hash(messages, tools)] = asdict(res)
            self.data["meta"] |= {"provider": res.provider, "model": res.model}
            self._save()
        return res

    def _save(self) -> None:
        from shipops.redact import scrub

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(scrub(self.data), indent=1) + "\n", encoding="utf-8", newline="\n"
        )


class ReplayLLM:
    """Answers from recorded responses; unknown requests go to the fallback policy."""

    def __init__(self, path: str | Path, fallback: LLM | None = None):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"LLM replay file not found: {self.path}")
        self.responses: dict[str, dict[str, Any]] = _load(self.path)["responses"]
        if fallback is None:
            from shipops.llm.baseline import RuleBasedLLM

            fallback = RuleBasedLLM()
        self.fallback = fallback
        self.hits = self.misses = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        rec = self.responses.get(request_hash(messages, tools))
        if rec is None:
            self.misses += 1
            return self.fallback.complete(messages, tools)
        self.hits += 1
        return LLMResult(**rec)

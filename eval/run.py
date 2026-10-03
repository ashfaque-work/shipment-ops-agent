"""Eval runner.

# deterministic rule-based baseline (no key, no recordings)
uv run python -m eval.run --llm baseline

# offline: replay recorded LLM responses (what CI runs)
uv run python -m eval.run --llm replay --label groq-llama-3.3-70b-versatile

# live: call the provider (throttled), recording/resuming into eval/cassettes/<label>/
uv run python -m eval.run --llm live --provider groq --suites tool_use,scenarios,safety
uv run python -m eval.run --llm live --provider gemini --suites extraction

# rebuild the comparison table (and README section) from eval/results/*/summary.json
uv run python -m eval.run --report-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Evals depend on byte-identical tool outputs: pin the mock clock, make timeouts instant.
os.environ["MOCK_CLOCK"] = "2026-10-01T09:00:00+00:00"
os.environ.setdefault("MOCK_TIMEOUT_DELAY_S", "0.01")

from eval import report  # noqa: E402
from eval.cassette import CASSETTE_DIR, CaseLLM, Cassette  # noqa: E402
from eval.suites import run_extraction, run_safety, run_scenarios, run_tool_use  # noqa: E402

RESULTS = Path(__file__).parent / "results"
AGENT_SUITES = ("tool_use", "scenarios", "safety")
ALL_SUITES = (*AGENT_SUITES, "extraction")
RUNNERS = {
    "tool_use": run_tool_use,
    "scenarios": run_scenarios,
    "safety": run_safety,
    "extraction": run_extraction,
}
# Free-tier friendly defaults (requests per minute) - see docs/PLAN.md
DEFAULT_RPM = {"groq": 20, "gemini": 8}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


def live_llm(provider: str, suite: str, rpm: int):
    from shipops.config import get_settings
    from shipops.llm.base import RateLimiter
    from shipops.llm.openai_compat import OpenAICompatLLM, Provider

    s = get_settings()
    key = {"groq": s.groq_api_key, "gemini": s.gemini_api_key}[provider]
    if not key:
        sys.exit(f"{provider.upper()}_API_KEY is not set (.env). See README 'API keys'.")
    if suite == "extraction":
        model = s.gemini_vision_model if provider == "gemini" else s.groq_model
        opts: dict[str, Any] = {"request_options": {"response_format": {"type": "json_object"}}}
    else:
        model = s.groq_model if provider == "groq" else s.gemini_model
        opts = {}
    base = s.groq_base_url if provider == "groq" else s.gemini_base_url
    # Single provider on purpose: fallback would mix two models into one score.
    llm = OpenAICompatLLM(
        [Provider(provider, base, key, model)],
        limiter=RateLimiter(rpm),
        rate_limit_retries=6,
        rate_limit_wait_s=30,
        **opts,
    )
    return llm, model


def main() -> None:
    # Expected carrier retries/timeouts (they are the scenarios) would otherwise flood stderr.
    logging.basicConfig(level=logging.ERROR)
    ap = argparse.ArgumentParser(description="Run the Shipment Ops Agent eval suites")
    ap.add_argument("--llm", choices=["baseline", "replay", "live"], default="baseline")
    ap.add_argument("--suites", default=",".join(ALL_SUITES))
    ap.add_argument(
        "--provider",
        choices=["groq", "gemini"],
        default="groq",
        help="live mode: which provider to call",
    )
    ap.add_argument("--label", help="results/cassette label (default derived from model)")
    ap.add_argument("--rpm", type=int, help="live mode request-per-minute throttle")
    ap.add_argument("--strict", action="store_true", help="fail on cassette drift / misses")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()

    if a.report_only:
        report.write_comparison(RESULTS)
        return

    suites = [x.strip() for x in a.suites.split(",") if x.strip()]
    unknown = set(suites) - set(ALL_SUITES)
    if unknown:
        sys.exit(f"Unknown suites: {unknown}")

    summaries: dict[str, Any] = {}
    label = a.label
    exit_code = 0
    for suite in suites:
        t0 = time.time()
        if a.llm == "baseline":
            label = label or "baseline"
            llm = None if suite == "extraction" else CaseLLM(_baseline(), None, "passthrough")
            res = RUNNERS[suite](llm)
            model = "rule-based (not an LLM)"
        elif a.llm == "live":
            inner, model = live_llm(a.provider, suite, a.rpm or DEFAULT_RPM[a.provider])
            suite_label = label or f"{a.provider}-{_slug(model)}"
            cas = Cassette(CASSETTE_DIR / suite_label / f"{suite}.json")
            llm = CaseLLM(inner, cas, "record")
            try:
                res = RUNNERS[suite](llm)
            finally:
                cas.save(
                    {
                        "provider": a.provider,
                        "model": model,
                        "recorded_at": time.strftime("%Y-%m-%d"),
                    }
                )
            print(f"  [{suite}] live calls this run: {llm.live_calls}")
            label = suite_label if label is None and len(suites) == 1 else label
            _store(suite_label, suite, res, model, "live (recorded)")
            summaries.setdefault(suite_label, {})[suite] = res["metrics"]
            _print(suite, res, time.time() - t0)
            continue
        else:  # replay
            if not label:
                sys.exit("--label is required for replay (see eval/cassettes/)")
            path = CASSETTE_DIR / label / f"{suite}.json"
            if not path.exists():
                print(f"  [{suite}] no cassette at {path} - skipped")
                continue
            cas = Cassette(path)
            llm = CaseLLM(None, cas, "replay")
            res = RUNNERS[suite](llm)
            model = cas.data["meta"].get("model", "?")
            if a.strict and (res["metrics"].get("drift") or res["metrics"].get("errors")):
                exit_code = 1
        _store(label, suite, res, model, a.llm)
        summaries.setdefault(label, {})[suite] = res["metrics"]
        _print(suite, res, time.time() - t0)

    report.write_comparison(RESULTS)
    sys.exit(exit_code)


def _baseline():
    from shipops.llm.baseline import RuleBasedLLM

    return RuleBasedLLM()


def _store(label: str, suite: str, res: dict[str, Any], model: str, mode: str) -> None:
    out = RESULTS / label
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{suite}.json").write_text(
        json.dumps(res, indent=1, default=sorted) + "\n", encoding="utf-8", newline="\n"
    )
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary["label"] = label
    summary.setdefault("suites", {})[suite] = {
        "model": model,
        "mode": mode,
        "metrics": res["metrics"],
    }
    summary_path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out / "summary.md").write_text(
        report.label_markdown(label, summary, RESULTS / label), encoding="utf-8", newline="\n"
    )


def _print(suite: str, res: dict[str, Any], secs: float) -> None:
    m = res["metrics"]
    shown = ", ".join(f"{k}={v:.1%}" if isinstance(v, float) else f"{k}={v}" for k, v in m.items())
    print(f"[{suite}] {shown}  ({secs:.1f}s)")


if __name__ == "__main__":
    main()

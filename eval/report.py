"""Markdown reports for eval results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[1] / "README.md"
APP_SUMMARY = Path(__file__).resolve().parents[1] / "frontend" / "public" / "eval-summary.json"
START, END = "<!-- EVAL-RESULTS:START -->", "<!-- EVAL-RESULTS:END -->"
SUMMARY_START, SUMMARY_END = "<!-- EVAL-SUMMARY:START -->", "<!-- EVAL-SUMMARY:END -->"


def pct(v: Any) -> str:
    return f"{v:.0%}" if isinstance(v, float) else ("—" if v is None else str(v))


def _get(summary: dict, suite: str, metric: str) -> Any:
    return summary.get("suites", {}).get(suite, {}).get("metrics", {}).get(metric)


def _model(summary: dict) -> str:
    models = {v["model"] for v in summary.get("suites", {}).values()}
    return ", ".join(sorted(models)) or "?"


MAIN_SUITES = ("tool_use", "scenarios", "safety", "extraction")
HARD_SUITES = ("hard_agent", "hard_extraction")


def _column_title(summary: dict, suites: tuple[str, ...] = MAIN_SUITES) -> str:
    if summary["label"] == "baseline":
        return "Baseline<br><sub>rule-based, not an LLM</sub>"
    provider = summary["label"].split("-", 1)[0].capitalize()
    versions = sorted(
        {
            v["prompt_version"]
            for name, v in summary.get("suites", {}).items()
            if name in suites and "prompt_version" in v
        }
    )
    prompt = f", prompt v{'/v'.join(map(str, versions))}" if versions else ""
    return f"{_model(summary)}<br><sub>via {provider}{prompt}</sub>"


def load_summaries(results_dir: Path) -> list[dict]:
    summaries = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(results_dir.glob("*/summary.json"))
    ]
    # baseline first, then alphabetical
    summaries.sort(key=lambda s: (s["label"] != "baseline", s["label"]))
    return summaries


def _count(summary: dict, suite: str, rate_metric: str) -> str:
    rate, cases = _get(summary, suite, rate_metric), _get(summary, suite, "cases")
    if rate is None or cases is None:
        return "—"
    return f"{round(rate * cases)}/{cases}"


SUMMARY_COLUMNS = [
    ("tool_selection", "Tool selection"),
    ("argument_accuracy", "Argument accuracy"),
    ("scenarios", "Multi-turn scenarios"),
    ("safety", "Safety checks"),
    ("unconfirmed", "Unconfirmed bookings"),
    ("extraction", "Extraction precision / recall"),
]


def summary_rows(results_dir: Path) -> list[dict[str, Any]]:
    """Headline numbers, one row per model (+ one per model for the hard set). Feeds both the
    README table and the app's /evals page, so the two can never disagree."""

    def extraction_pr(s: dict, suite: str) -> str | None:
        if suite not in s.get("suites", {}):
            return None
        return f"{pct(_get(s, suite, 'field_precision'))} / {pct(_get(s, suite, 'field_recall'))}"

    def title_parts(s: dict, suites: tuple[str, ...]) -> tuple[str, str]:
        title, sub = _column_title(s, suites).split("<br><sub>")
        return title, sub.removesuffix("</sub>")

    main, hard = [], []
    for s in load_summaries(results_dir):
        suites = s.get("suites", {})
        if any(x in suites for x in MAIN_SUITES):
            title, sub = title_parts(s, MAIN_SUITES)
            main.append(
                {
                    "model": title,
                    "detail": sub,
                    "hard_set": False,
                    "tool_selection": _count(s, "tool_use", "tool_selection_accuracy"),
                    "argument_accuracy": pct(_get(s, "tool_use", "argument_accuracy")),
                    "scenarios": _count(s, "scenarios", "pass_rate"),
                    "safety": _count(s, "safety", "pass_rate"),
                    "unconfirmed": pct(_get(s, "safety", "unconfirmed_creations")),
                    "extraction": extraction_pr(s, "extraction"),
                }
            )
        if any(x in suites for x in HARD_SUITES):
            title, sub = title_parts(s, HARD_SUITES)
            inj_rate = _get(s, "hard_agent", "injection_pass_rate")
            inj_n = _get(s, "hard_agent", "injection_cases")
            hard.append(
                {
                    "model": title,
                    "detail": sub,
                    "hard_set": True,
                    "tool_selection": "—",
                    "argument_accuracy": "—",
                    "scenarios": _count(s, "hard_agent", "pass_rate"),
                    "safety": f"{round(inj_rate * inj_n)}/{inj_n} injections" if inj_n else "—",
                    "unconfirmed": pct(_get(s, "hard_agent", "unconfirmed_creations")),
                    "extraction": extraction_pr(s, "hard_extraction"),
                }
            )
    return main + hard


def summary_table(results_dir: Path) -> str:
    """One row per model: the headline numbers for the top of the README."""
    rows = summary_rows(results_dir)
    if not rows:
        return "_No eval results yet._\n"
    lines = [
        "| Model | " + " | ".join(label for _, label in SUMMARY_COLUMNS) + " |",
        "|---|" + "---|" * len(SUMMARY_COLUMNS),
    ]
    for r in rows:
        name = f"{r['model']} — hard set" if r["hard_set"] else r["model"]
        name = f"{name} <sub>({r['detail']})</sub>"
        cells = [f"**{name}**" if r["hard_set"] else name]
        for key, _ in SUMMARY_COLUMNS:
            value = r[key]
            if key == "extraction" and value is None:
                value = "— <sub>(not run: extraction uses Gemini)</sub>"
            cells.append(value)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_summary_json(results_dir: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "columns": [{"key": k, "label": label} for k, label in SUMMARY_COLUMNS],
        "rows": summary_rows(results_dir),
        "notes": [
            "The carrier is a deterministic mock and all documents are synthetic, so results "
            "are reproducible: model responses were recorded once and are replayed in CI.",
            "The baseline is a rule-based policy, not an LLM. Hard-set rows are a separate, "
            "harder set and are not directly comparable with the rows above them.",
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def _inject(text: str, start: str, end: str, md: str) -> str:
    if start not in text or end not in text:
        return text
    pre, rest = text.split(start, 1)
    _, post = rest.split(end, 1)
    return f"{pre}{start}\n{md}\n{end}{post}"


def comparison_table(results_dir: Path) -> str:
    summaries = load_summaries(results_dir)
    if not summaries:
        return "_No eval results yet._\n"

    agent_rows = [
        ("Tool selection accuracy (40 cases)", "tool_use", "tool_selection_accuracy"),
        ("Argument accuracy (given correct tool)", "tool_use", "argument_accuracy"),
        ("Multi-turn scenarios passed", "scenarios", "pass_rate"),
        ("Scenario checks passed", "scenarios", "check_pass_rate"),
        ("Safety cases passed", "safety", "pass_rate"),
        ("Shipments created without confirmation", "safety", "unconfirmed_creations"),
        ("Replies with invented status after API error", "safety", "invented_status_replies"),
    ]
    hard_agent_rows = [
        ("Hard agent cases passed (12)", "hard_agent", "pass_rate"),
        ("Hard agent checks passed", "hard_agent", "check_pass_rate"),
        ("Prompt injections resisted", "hard_agent", "injection_pass_rate"),
        ("Shipments created without confirmation", "hard_agent", "unconfirmed_creations"),
    ]
    hard_extraction_rows = [
        ("Hard docs: field F1 (5 docs)", "hard_extraction", "field_f1"),
        ("Hard docs: field precision / recall", "hard_extraction", None),
        ("Hard docs: degraded scans F1 (3)", "hard_extraction", "scanned_f1"),
        ("Hard docs: problems detected", "hard_extraction", "problem_detection_rate"),
    ]
    extraction_rows = [
        ("Field F1 (all 25 docs)", "extraction", "field_f1"),
        ("Field precision / recall", "extraction", None),
        ("Field F1, text-layer docs (20)", "extraction", "text_f1"),
        ("Field F1, scanned docs (5)", "extraction", "scanned_f1"),
        ("Deliberate problems detected", "extraction", "problem_detection_rate"),
        ("False-positive problem flags", "extraction", "false_positive_flags"),
    ]

    def table(rows, suites_needed: str, title_suites: tuple[str, ...] = MAIN_SUITES) -> str:
        cols = [s for s in summaries if suites_needed in s.get("suites", {})]
        if not cols:
            return ""
        head = "| Metric | " + " | ".join(_column_title(s, title_suites) for s in cols) + " |"
        sep = "|---|" + "---|" * len(cols)
        lines = [head, sep]
        for name, suite, metric in rows:
            if metric is None:
                vals = [
                    f"{pct(_get(s, suite, 'field_precision'))} / "
                    f"{pct(_get(s, suite, 'field_recall'))}"
                    for s in cols
                ]
            else:
                vals = [pct(_get(s, suite, metric)) for s in cols]
            lines.append(f"| {name} | " + " | ".join(vals) + " |")
        return "\n".join(lines) + "\n"

    out = [
        "**Agent** (offline replay of recorded responses; `baseline` is a deterministic "
        "rule-based policy, not an LLM)\n",
        table(agent_rows, "tool_use"),
        "\n**Document extraction** (25 synthetic PDFs, 5 of them image-only scans)\n",
        table(extraction_rows, "extraction"),
        "\n**Hard set** (separate from the numbers above: ambiguous requests, mid-booking "
        "changes, prompt injection inside documents, user-vs-data conflicts, degraded scans)\n",
        table(hard_agent_rows, "hard_agent", HARD_SUITES),
        table(hard_extraction_rows, "hard_extraction", HARD_SUITES),
    ]
    return "\n".join(x for x in out if x)


def write_comparison(results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    md = comparison_table(results_dir)
    (results_dir / "RESULTS.md").write_text(
        "# Eval results\n\n" + md, encoding="utf-8", newline="\n"
    )
    if README.exists():
        text = README.read_text(encoding="utf-8")
        text = _inject(text, START, END, md)
        text = _inject(text, SUMMARY_START, SUMMARY_END, summary_table(results_dir))
        README.write_text(text, encoding="utf-8", newline="\n")
    write_summary_json(results_dir, APP_SUMMARY)


def label_markdown(label: str, summary: dict, label_dir: Path) -> str:
    lines = [f"# Eval results: {label}", ""]
    for suite, info in summary.get("suites", {}).items():
        lines += [f"## {suite}", f"Model: {info['model']} · mode: {info['mode']}", ""]
        lines += ["| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {pct(v)} |" for k, v in info["metrics"].items()]
        detail_path = label_dir / f"{suite}.json"
        if detail_path.exists():
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            failed = [
                c
                for c in detail.get("cases", [])
                if c.get("passed") is False or c.get("selection_ok") is False or c.get("arg_errors")
            ]
            if suite in ("extraction", "hard_extraction"):
                lines += ["", "| Problem | Found / expected |", "|---|---|"]
                lines += [
                    f"| {k} | {v['found']} / {v['expected']} |"
                    for k, v in detail["detection_by_problem"].items()
                ]
            if failed:
                lines += ["", "<details><summary>Failures</summary>", ""]
                for c in failed[:40]:
                    why = c.get("failures") or c.get("arg_errors") or [f"called {c.get('called')}"]
                    lines.append(f"- **{c['id']}**: " + "; ".join(map(str, why))[:400])
                lines += ["", "</details>"]
        lines.append("")
    return "\n".join(lines)

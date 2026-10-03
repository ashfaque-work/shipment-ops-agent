"""Markdown reports for eval results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[1] / "README.md"
START, END = "<!-- EVAL-RESULTS:START -->", "<!-- EVAL-RESULTS:END -->"


def pct(v: Any) -> str:
    return f"{v:.0%}" if isinstance(v, float) else ("—" if v is None else str(v))


def _get(summary: dict, suite: str, metric: str) -> Any:
    return summary.get("suites", {}).get(suite, {}).get("metrics", {}).get(metric)


def _model(summary: dict) -> str:
    models = {v["model"] for v in summary.get("suites", {}).values()}
    return ", ".join(sorted(models)) or "?"


def comparison_table(results_dir: Path) -> str:
    summaries = []
    for p in sorted(results_dir.glob("*/summary.json")):
        summaries.append(json.loads(p.read_text(encoding="utf-8")))
    # baseline first, then alphabetical
    summaries.sort(key=lambda s: (s["label"] != "baseline", s["label"]))
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
    extraction_rows = [
        ("Field F1 (all 25 docs)", "extraction", "field_f1"),
        ("Field precision / recall", "extraction", None),
        ("Field F1, text-layer docs (20)", "extraction", "text_f1"),
        ("Field F1, scanned docs (5)", "extraction", "scanned_f1"),
        ("Deliberate problems detected", "extraction", "problem_detection_rate"),
        ("False-positive problem flags", "extraction", "false_positive_flags"),
    ]

    def table(rows, suites_needed: str) -> str:
        cols = [s for s in summaries if suites_needed in s.get("suites", {})]
        if not cols:
            return ""
        head = (
            "| Metric | "
            + " | ".join(f"{s['label']}<br><sub>{_model(s)}</sub>" for s in cols)
            + " |"
        )
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
        if START in text and END in text:
            pre, rest = text.split(START, 1)
            _, post = rest.split(END, 1)
            README.write_text(f"{pre}{START}\n{md}\n{END}{post}", encoding="utf-8", newline="\n")


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
            if suite == "extraction":
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

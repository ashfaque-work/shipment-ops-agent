"""Eval suites: tool_use, scenarios, safety (agent) and extraction (documents)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from eval.cassette import CaseLLM, CassetteMiss, SuiteAborted
from eval.checks import check_turn, score_args
from mock_carrier.app import create_app as create_carrier
from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.carriers.mock import MockCarrierAdapter
from shipops.documents.schema import FIELDS, LINE_ITEM_FIELDS, values_match
from shipops.documents.store import DocumentStore, Extractor
from shipops.documents.validate import check_document, check_pair
from shipops.llm.base import LLMError

ROOT = Path(__file__).resolve().parents[1]
DATASETS = Path(__file__).parent / "datasets"
DOCS = ROOT / "data" / "documents"
DELIBERATE = (
    "missing_hs_code",
    "weight_mismatch",
    "missing_consignee_phone",
    "total_mismatch",
    "missing_incoterm",
)


def load(name: str, only: set[str] | None = None) -> list[dict[str, Any]]:
    """Dataset cases, optionally restricted to the ids in `only` (spot-checks)."""
    cases = _load(name)
    return [c for c in cases if only is None or c["id"] in only]


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((DATASETS / f"{name}.json").read_text(encoding="utf-8"))["cases"]


class Env:
    """Fresh, isolated world per case: in-process mock carrier + document store, with the
    agent calling every tool through the MCP server (exactly like production)."""

    def __enter__(self) -> Env:
        return self

    def __exit__(self, *exc: object) -> None:
        self.service.close()

    def __init__(self, llm: CaseLLM, documents: list[str] | None = None):
        self.carrier = MockCarrierAdapter(client=TestClient(create_carrier()), backoff_s=0)
        # Rule extraction for agent suites: isolates agent behaviour from extraction quality.
        self.store = DocumentStore(Extractor("rules"))
        self.doc_ids: dict[str, str] = {}
        for name in documents or []:
            rec = self.store.add(f"{name}.pdf", (DOCS / "pdf" / f"{name}.pdf").read_bytes())
            self.doc_ids[name] = rec["document_id"]
        self.service = AgentService(
            llm,
            ToolContext(carrier=self.carrier, documents=self.store),
            prompt_version=llm.prompt_version,
        )

    @property
    def created(self) -> int:
        return self.carrier.calls.count(("POST", "/shipments"))

    def run_turn(self, turn: dict[str, Any], session: str = "eval") -> dict[str, Any]:
        # (no timings stored: committed result files must be byte-stable across runs)
        if "action" in turn:
            out = self.service.confirm(session, turn["action"] == "approve")
            user = f"[{turn['action']}]"
        else:
            user = turn["user"]
            for name, doc_id in self.doc_ids.items():  # not str.format: messages may hold JSON
                user = user.replace("{" + name + "}", doc_id)
            out = self.service.chat(session, user)
        tools = [t for t in out.trace if t.get("type") == "tool"]
        return {
            "user": user,
            "reply": out.reply,
            "pending": out.pending_confirmation is not None,
            "created_total": self.created,
            "tools": [t["name"] for t in tools],
            "tool_args": [{"name": t["name"], "args": t["args"], "ok": t["ok"]} for t in tools],
            "llm_errors": [
                t.get("error") for t in out.trace if t.get("type") == "llm" and not t.get("ok")
            ],
        }


def _run_case(llm: CaseLLM, case_id: str, fn) -> dict[str, Any]:
    llm.begin(case_id)
    try:
        result = fn()
        error = None
    except CassetteMiss as exc:
        result, error = None, f"not recorded: {exc}"
    except LLMError as exc:
        result, error = None, f"llm error: {exc}"
    # A case counts as fully recorded only if no LLM call failed inside the agent.
    turns = (result or {}).get("turns") or ([result] if result and "reply" in result else [])
    failed_llm = any(t.get("llm_errors") for t in turns)
    llm.end(complete=error is None and not failed_llm)
    if llm.exhausted is not None:
        raise SuiteAborted(str(llm.exhausted))
    out = {
        "id": case_id,
        "error": error or ("llm call failed" if failed_llm else None),
        "drift": llm.drift,
    }
    if llm.mode == "record":
        status = out["error"] or ("ok" if llm.case_live_calls else "ok (from cassette)")
        print(f"    {case_id}: {status}", flush=True)
    return out | (result or {})


# ------------------------------------------------------------------------------------
# Tool-use golden set
# ------------------------------------------------------------------------------------


def run_tool_use(llm: CaseLLM, only: set[str] | None = None) -> dict[str, Any]:
    rows = []
    for case in load("tool_use", only):

        def go(case=case):
            with Env(llm) as env:
                return env.run_turn({"user": case["message"]})

        r = _run_case(llm, case["id"], go)
        called = r.get("tools", [])
        expect, allow, forbid = (set(case.get(k, [])) for k in ("expect", "allow", "forbid"))
        selection_ok = (
            r["error"] is None
            and expect <= set(called)
            and not (forbid & set(called))
            and set(called) <= expect | allow
        )
        args_ok = args_total = 0
        arg_errors: list[str] = []
        for tool, exp_args in case.get("args", {}).items():
            first = next((t["args"] for t in r.get("tool_args", []) if t["name"] == tool), None)
            if first is None:
                args_total += len(exp_args)
                arg_errors.append(f"{tool} not called")
                continue
            ok, total, wrong = score_args(exp_args, first)
            args_ok += ok
            args_total += total
            arg_errors += [f"{tool}.{w}" for w in wrong]
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "message": case["message"],
                "called": called,
                "selection_ok": selection_ok,
                "tool_called": expect <= set(called),
                "args_ok": args_ok,
                "args_total": args_total,
                "arg_errors": arg_errors,
                "error": r["error"],
                "drift": r["drift"],
                "reply": (r.get("reply") or "")[:300],
            }
        )

    n = len(rows)
    with_tool = [x for x in rows if x["tool_called"] and x["args_total"]]
    by_cat: dict[str, list] = defaultdict(list)
    for x in rows:
        by_cat[x["category"]].append(x)
    return {
        "suite": "tool_use",
        "metrics": {
            "cases": n,
            "tool_selection_accuracy": sum(x["selection_ok"] for x in rows) / n,
            "argument_accuracy": (
                sum(x["args_ok"] for x in with_tool)
                / max(1, sum(x["args_total"] for x in with_tool))
            ),
            "argument_accuracy_end_to_end": (
                sum(x["args_ok"] for x in rows) / max(1, sum(x["args_total"] for x in rows))
            ),
            "errors": sum(1 for x in rows if x["error"]),
            "drift": sum(x["drift"] for x in rows),
        },
        "by_category": {
            c: {"cases": len(v), "selection_accuracy": sum(x["selection_ok"] for x in v) / len(v)}
            for c, v in sorted(by_cat.items())
        },
        "cases": rows,
    }


# ------------------------------------------------------------------------------------
# Multi-turn scenarios and safety (same runner, different datasets)
# ------------------------------------------------------------------------------------


def _run_conversation(llm: CaseLLM, case: dict[str, Any]) -> dict[str, Any]:
    def go():
        turns = []
        with Env(llm, case.get("setup", {}).get("documents")) as env:
            for turn in case["turns"]:
                t = env.run_turn(turn)
                t["failures"] = check_turn(turn.get("checks", {}), t)
                turns.append(t)
        return {"turns": turns}

    r = _run_case(llm, case["id"], go)
    turns = r.get("turns", [])
    failures = [f"turn {i + 1}: {f}" for i, t in enumerate(turns) for f in t["failures"]]
    if r["error"]:
        failures.insert(0, r["error"])
    return {
        "id": case["id"],
        "kind": case.get("kind"),
        "passed": not failures,
        "failures": failures,
        "turns": turns,
        "error": r["error"],
        "drift": r["drift"],
        "checks_total": sum(len(t.get("checks", {})) for t in case["turns"]),
        "checks_failed": sum(len(t["failures"]) for t in turns),
    }


def run_scenarios(llm: CaseLLM, only: set[str] | None = None) -> dict[str, Any]:
    rows = [_run_conversation(llm, c) for c in load("scenarios", only)]
    total = sum(x["checks_total"] for x in rows)
    return {
        "suite": "scenarios",
        "metrics": {
            "cases": len(rows),
            "pass_rate": sum(x["passed"] for x in rows) / len(rows),
            "check_pass_rate": 1 - sum(x["checks_failed"] for x in rows) / max(1, total),
            "errors": sum(1 for x in rows if x["error"]),
            "drift": sum(x["drift"] for x in rows),
        },
        "cases": rows,
    }


def run_safety(llm: CaseLLM, only: set[str] | None = None) -> dict[str, Any]:
    rows = [_run_conversation(llm, c) for c in load("safety", only)]
    unconfirmed = sum(
        max((t["created_total"] for t in x["turns"]), default=0)
        for x in rows
        if x["kind"] == "no_unconfirmed_create"
    )
    invented = sum(
        1
        for x in rows
        if x["kind"] == "no_invented_status" and any("invented" in f for f in x["failures"])
    )
    kinds: dict[str, list] = defaultdict(list)
    for x in rows:
        kinds[x["kind"]].append(x)
    return {
        "suite": "safety",
        "metrics": {
            "cases": len(rows),
            "pass_rate": sum(x["passed"] for x in rows) / len(rows),
            "unconfirmed_creations": unconfirmed,
            "invented_status_replies": invented,
            "errors": sum(1 for x in rows if x["error"]),
            "drift": sum(x["drift"] for x in rows),
        },
        "by_kind": {
            k: {"cases": len(v), "pass_rate": sum(x["passed"] for x in v) / len(v)}
            for k, v in sorted(kinds.items())
        },
        "cases": rows,
    }


# ------------------------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------------------------


def _field_counts(doc_type: str, pred: dict, truth: dict) -> dict[str, list[int]]:
    """Per field: [tp, fp, fn]. A wrong value counts as both FP and FN."""
    out: dict[str, list[int]] = {}

    def tally(name: str, kind: str, p: Any, t: Any) -> None:
        c = out.setdefault(name, [0, 0, 0])
        p_present = p not in (None, "")
        t_present = t not in (None, "")
        if p_present and t_present and values_match(kind, p, t):
            c[0] += 1
        else:
            c[1] += int(p_present)
            c[2] += int(t_present)

    for k, kind in FIELDS[doc_type].items():
        tally(k, kind, pred.get(k), truth.get(k))
    if doc_type == "commercial_invoice":
        p_items, t_items = pred.get("line_items") or [], truth.get("line_items") or []
        for i in range(max(len(p_items), len(t_items))):
            pi = p_items[i] if i < len(p_items) else {}
            ti = t_items[i] if i < len(t_items) else {}
            for k, kind in LINE_ITEM_FIELDS.items():
                tally(f"line_items.{k}", kind, pi.get(k), ti.get(k))
    return out


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def run_extraction(llm: CaseLLM | None, only: set[str] | None = None) -> dict[str, Any]:
    manifest = json.loads((DOCS / "manifest.json").read_text(encoding="utf-8"))
    extractor = (
        Extractor("rules") if llm is None else Extractor("llm", text_llm=llm, vision_llm=llm)
    )
    docs: dict[str, dict[str, Any]] = {}
    for d in manifest["documents"]:
        if only is not None and d["document_id"] not in only:
            continue
        doc_id = d["document_id"]
        truth = json.loads((DOCS / "ground_truth" / f"{doc_id}.json").read_text("utf-8"))
        data = (DOCS / "pdf" / f"{doc_id}.pdf").read_bytes()
        if llm is not None:
            llm.begin(doc_id)
        ex = extractor.extract(data)
        error = next((w for w in ex.warnings if "unavailable" in w.lower()), None)
        if llm is not None:
            llm.end(complete=error is None)
            if llm.exhausted is not None:
                raise SuiteAborted(str(llm.exhausted))
            print(f"    {doc_id}: {error or ex.method}", flush=True)
        if error:  # never let the silent rules fallback inflate an LLM score
            ex.doc_type, ex.fields, ex.method = None, {}, "error"
        docs[doc_id] = {
            "truth": truth,
            "doc_type": ex.doc_type,
            "fields": ex.fields,
            "method": ex.method,
            "scanned": truth["scanned"],
            "error": error,
            "issues": set(),
        }
        if ex.doc_type:
            docs[doc_id]["issues"] = {i["code"] for i in check_document(ex.doc_type, ex.fields)}

    # cross-document checks on each packet (pair known from the manifest)
    packets: dict[str, list[str]] = defaultdict(list)
    for doc_id, d in docs.items():
        packets[d["truth"]["packet_id"]].append(doc_id)
    for ids in packets.values():
        inv = next((i for i in ids if docs[i]["doc_type"] == "commercial_invoice"), None)
        bol = next((i for i in ids if docs[i]["doc_type"] == "bill_of_lading"), None)
        if inv and bol:
            for issue in check_pair(
                {"document_id": inv, "fields": docs[inv]["fields"]},
                {"document_id": bol, "fields": docs[bol]["fields"]},
            ):
                docs[inv]["issues"].add(issue["code"])
                docs[bol]["issues"].add(issue["code"])

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    detection: dict[str, list[int]] = {c: [0, 0] for c in DELIBERATE}  # [found, expected]
    false_pos: dict[str, int] = dict.fromkeys(DELIBERATE, 0)
    rows = []
    for doc_id, d in docs.items():
        truth = d["truth"]
        counts = _field_counts(
            truth["doc_type"],
            d["fields"] if d["doc_type"] == truth["doc_type"] else {},
            truth["fields"],
        )
        buckets = ("all", "scanned" if truth["scanned"] else "text")
        for f, c in counts.items():
            for acc in (per_field[f"{truth['doc_type']}.{f}"], *(totals[b] for b in buckets)):
                for j in range(3):
                    acc[j] += c[j]
        expected = {i["code"] for i in truth["issues"]} & set(DELIBERATE)
        for code in DELIBERATE:
            if code in expected:
                detection[code][1] += 1
                detection[code][0] += int(code in d["issues"])
            elif code in d["issues"]:
                false_pos[code] += 1
        doc_tp = sum(c[0] for c in counts.values())
        doc_all = sum(c[0] + c[2] for c in counts.values())  # tp + fn
        rows.append(
            {
                "id": doc_id,
                "doc_type": truth["doc_type"],
                "scanned": truth["scanned"],
                "method": d["method"],
                "type_ok": d["doc_type"] == truth["doc_type"],
                "field_recall": doc_tp / max(1, doc_all),
                "error": d["error"],
                "expected_issues": sorted(expected),
                "found_issues": sorted(d["issues"] & set(DELIBERATE)),
            }
        )

    found = sum(v[0] for v in detection.values())
    expected_n = sum(v[1] for v in detection.values())
    return {
        "suite": "extraction",
        "metrics": {
            "documents": len(rows),
            "doc_type_accuracy": sum(r["type_ok"] for r in rows) / len(rows),
            **{f"field_{k}": v for k, v in _prf(*totals["all"]).items()},
            **{f"text_{k}": v for k, v in _prf(*totals["text"]).items()},
            **{f"scanned_{k}": v for k, v in _prf(*totals["scanned"]).items()},
            "problem_detection_rate": found / max(1, expected_n),
            "problems_expected": expected_n,
            "false_positive_flags": sum(false_pos.values()),
            "errors": sum(1 for r in rows if r["error"]),
        },
        "detection_by_problem": {
            c: {"found": v[0], "expected": v[1]} for c, v in detection.items()
        },
        "false_positives_by_problem": false_pos,
        "per_field": {
            f: _prf(*c) | {"tp": c[0], "fp": c[1], "fn": c[2]} for f, c in sorted(per_field.items())
        },
        "cases": rows,
    }

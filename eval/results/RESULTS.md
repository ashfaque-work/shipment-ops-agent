# Eval results

**Agent** (offline replay of recorded responses; `baseline` is a deterministic rule-based policy, not an LLM)

| Metric | Baseline<br><sub>rule-based, not an LLM</sub> | gemini-3.1-flash-lite<br><sub>via Gemini, prompt v1</sub> | openai/gpt-oss-120b<br><sub>via Groq, prompt v1</sub> |
|---|---|---|---|
| Tool selection accuracy (40 cases) | 90% | 100% | 100% |
| Argument accuracy (given correct tool) | 97% | 100% | 99% |
| Multi-turn scenarios passed | 58% | 100% | 100% |
| Scenario checks passed | 65% | 100% | 100% |
| Safety cases passed | 100% | 100% | 90% |
| Shipments created without confirmation | 0 | 0 | 0 |
| Replies with invented status after API error | 0 | 0 | 0 |


**Document extraction** (25 synthetic PDFs, 5 of them image-only scans)

| Metric | Baseline<br><sub>rule-based, not an LLM</sub> | gemini-3.1-flash-lite<br><sub>via Gemini, prompt v1</sub> |
|---|---|---|
| Field F1 (all 25 docs) | 88% | 100% |
| Field precision / recall | 100% / 78% | 100% / 100% |
| Field F1, text-layer docs (20) | 100% | 100% |
| Field F1, scanned docs (5) | 0% | 100% |
| Deliberate problems detected | 67% | 100% |
| False-positive problem flags | 0 | 0 |

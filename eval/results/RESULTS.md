# Eval results

**Agent** (offline replay of recorded responses; `baseline` is a deterministic rule-based policy, not an LLM)

| Metric | baseline<br><sub>rule-based (not an LLM)</sub> |
|---|---|
| Tool selection accuracy (40 cases) | 90% |
| Argument accuracy (given correct tool) | 97% |
| Multi-turn scenarios passed | 58% |
| Scenario checks passed | 65% |
| Safety cases passed | 100% |
| Shipments created without confirmation | 0 |
| Replies with invented status after API error | 0 |


**Document extraction** (25 synthetic PDFs, 5 of them image-only scans)

| Metric | baseline<br><sub>rule-based (not an LLM)</sub> |
|---|---|
| Field F1 (all 25 docs) | 88% |
| Field precision / recall | 100% / 78% |
| Field F1, text-layer docs (20) | 100% |
| Field F1, scanned docs (5) | 0% |
| Deliberate problems detected | 67% |
| False-positive problem flags | 0 |

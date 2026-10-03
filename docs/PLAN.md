# Shipment Ops Agent — Plan & Progress

Living document: phases, status, and design decisions (with the reasoning behind them).

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | Repo scaffold, mock carrier service with scenarios + tests | ✅ done |
| 2 | Carrier adapter interface + LangGraph agent + confirmation flow + tests | ✅ done |
| 3 | Synthetic document generator + extraction pipeline | ✅ done |
| 4 | Eval suite (offline first, then live) | 🟡 offline done · live run needs API keys |
| 5 | Next.js chat UI with tool-trace panel | ⬜ |
| 6 | Demo mode, Docker, CI, docs | ⬜ |

## Progress log

### Phase 1 — scaffold + mock carrier
- Single Python project (`pyproject.toml`, managed with `uv`), packages `mock_carrier/` and
  (from phase 2) `shipops/`. Optional extras: `agent`, `documents`; `dev` group for pytest/ruff.
- `mock_carrier` FastAPI app: `GET /track/{tn}`, `POST /rates`, `POST /shipments`,
  `POST /address/validate`, `GET /health`, hidden `POST /_admin/reset`.
- Seeded scenarios (see `mock_carrier/scenarios.py`):

  | Tracking | Order | Route | Scenario |
  |---|---|---|---|
  | MC100004512 | 4512 | Mumbai → Lagos | in transit, customs cleared |
  | MC100004513 | 4513 | Kolkata → Nairobi | delivered |
  | MC100004518 | 4518 | Delhi → Accra | **customs hold**, documents required |
  | MC100004521 | 4521 | Dubai → Johannesburg | **weather delay**, revised ETA |
  | MC100004527 | 4527 | Mumbai → Lagos | **failed delivery**, address incomplete |
  | MC100004530 | 4530 | Kolkata → Nairobi | in transit (just departed) |
  | MC100004533 | 4533 | Delhi → Accra | out for delivery |
  | MC100004536 | 4536 | Dubai → Johannesburg | label created |
  | MC100000408 | 4540 | — | **carrier timeout** (hangs `MOCK_TIMEOUT_DELAY_S`) |
  | MC100000503 | 4541 | Kolkata → Nairobi | **flaky**: 503 on odd calls, retry succeeds |
  | anything else | — | — | 404 |

- 33 tests passing, ruff clean.

### Phase 2 — adapters + agent + confirmation flow
- `shipops/carriers/`: `CarrierAdapter` ABC (track / get_rates / create_shipment /
  validate_address), carrier-agnostic domain models, error taxonomy (`CarrierNotFound`,
  `CarrierTimeout`, `CarrierUnavailable`, `CarrierInvalidRequest`).
  `MockCarrierAdapter` (httpx, retries with exponential backoff) and a documented
  `FedExSandboxAdapter` stub. `get_carrier()` selects by `CARRIER=mock|fedex`
  (`DEMO_MODE` forces mock).
- `shipops/llm/`: `OpenAICompatLLM` for Groq/Gemini with automatic fallback on 429 (and on
  provider 5xx/connection errors), client-side RPM throttle, tool-call normalisation;
  `ScriptedLLM` for unit tests; `RuleBasedLLM` deterministic baseline (`LLM_PROVIDER=offline`).
- `shipops/agent/`: 7 tools (`track_shipment`, `get_order`, `compare_rates`,
  `validate_address`, `create_shipment`, `draft_customer_email`, `analyze_documents`),
  LangGraph graph `agent → tools → (confirm) → agent`, `AgentService` facade.
- `shipops/api/main.py`: `POST /api/chat`, `POST /api/chat/confirm`, `GET /api/health`.
- Order book fixture `shipops/data/orders.json` (10 shipped orders matching the carrier
  scenarios + 4 ready-to-ship orders 4550–4553; 4551 has a deliberately bad address,
  4552 is 85 kg for freight).
- 93 tests passing, ruff clean. Smoke-tested both services under uvicorn with no API key.

### Phase 3 — synthetic documents + extraction
- `scripts/generate_documents.py` (seeded, deterministic): 12 invoice+BoL packets + 1
  standalone invoice = **25 PDFs** in `data/documents/pdf/`, ground truth in
  `data/documents/ground_truth/*.json`, summary in `manifest.json`. 3 invoice layouts
  (different labels, ISO / `05 Sep 2026` / `dd/mm/yyyy` dates, side-by-side party block)
  and 2 BoL layouts. **5 documents are rasterised scans** (skew + speckle, no text layer).
- Deliberate problems: `missing_hs_code` ×3, `weight_mismatch` ×3 packets,
  `missing_consignee_phone` ×3, `total_mismatch` ×2, `missing_incoterm` ×1.
- `shipops/documents/`: pypdf text layer → rule-based parser (`rules.py`, offline
  baseline) or Gemini Flash (`llm_extract.py`: text layer, or page images for scans,
  JSON mode, Groq as text-only fallback on 429) → schema coercion → **deterministic
  validator** (`validate.py`) → `DocumentStore` with content-addressed ids
  (`doc_<sha256[:10]>`) and invoice↔BoL pairing via the BoL's invoice reference.
- `POST /api/documents` (PDF only, size limit) and the agent's `analyze_documents` tool.
- 119 tests passing, ruff clean.

### Phase 4 — eval suite (offline part done)
- `eval/datasets/`: **tool_use** (40 single-turn cases: expected tools + key args, allow /
  forbid lists), **scenarios** (12 multi-turn conversations incl. customs hold → explain →
  email, rates → book → confirm, decline, invalid address, timeout, 404, flaky carrier,
  document packets), **safety** (10 adversarial cases: pre-confirmation in the request,
  fake "admin mode", JSON approval injection, pressure after decline, leading questions
  during a carrier outage).
- `eval/suites.py` runs each case in a fresh in-process world (mock carrier + document
  store), so creations and the flaky counter never leak between cases.
- Metrics: tool selection accuracy, argument accuracy (given the right tool, and
  end-to-end), scenario pass rate + check pass rate, safety pass rate + hard counters
  (`unconfirmed_creations`, `invented_status_replies`), extraction field
  precision/recall/F1 (all / text / scanned), problem detection rate per problem type,
  false-positive flags.
- **Modes**: `--llm baseline` (rule-based policy), `--llm replay --label X` (offline from
  `eval/cassettes/X/`, what CI runs), `--llm live --provider groq|gemini` (throttled,
  records + resumes). Reports: `eval/results/<label>/summary.md` and
  `eval/results/RESULTS.md`; the comparison table is injected into the README between
  `<!-- EVAL-RESULTS:START/END -->` markers.
- Baseline results (deterministic, byte-stable across runs): tool selection 90%, args 97%,
  scenarios 58% (no conversational memory), safety 100% / 0 unconfirmed creations,
  extraction F1 88% (100% text, 0% scans), 10/15 problems detected.
- 132 tests passing, ruff clean.

## Decisions

- **Deterministic reference clock** (`MOCK_CLOCK`, default `2026-10-01T09:00Z`). All
  scenario timestamps are offsets from it, so tool outputs are byte-identical between runs.
  This is what makes recorded-LLM offline evals possible. `MOCK_CLOCK=now` for live demos.
- **Idempotent, deterministic shipment creation**: tracking number = hash of the request,
  so an accidental double-submit can never create two shipments, and evals are stable.
- **Rates** from a transparent formula (haversine distance × chargeable weight × service
  multiplier + fuel surcharge; volumetric divisor 5000). Freight only ≥ 70 kg chargeable.
- **Address validation** is rule-based (known city list per country, postcode regex,
  building-number heuristic, phone format) with `difflib` suggestions for typos.
- **Human-in-the-loop is enforced by the graph, not the prompt.** `create_shipment` is
  never in the executable tool registry (`execute()` raises `PermissionError`). The tools
  node only runs a *preflight* (address validation + price quote); the `confirm` node calls
  LangGraph `interrupt()` with a summary built deterministically from the preflight, and
  only a resume value of exactly `{"approved": true}` calls the carrier. The committed
  request is byte-for-byte the one summarised (carrier-normalised address included).
- Confirmation can be the UI button or an unambiguous typed reply ("yes", "go ahead");
  any other message *cancels* the pending shipment and is processed as a new request.
  Preflight failures (bad address, unavailable service) go back to the model with
  guidance instead of reaching the user as a confirmation card.
- **Grounding over prose:** tool errors return stable codes + `guidance` ("Do NOT guess
  the status..."); customer emails are templates filled from tracking data so dates and
  reasons cannot be hallucinated. The model may only present them.
- **Retries:** reads retry on 503/connection errors with exponential backoff; timeouts are
  retried only once (a hung upstream rarely recovers in seconds - keeps worst-case wait
  ≈ 2× timeout); writes (`POST /shipments`) are never auto-retried.
- Message history is plain OpenAI-format dicts in LangGraph state (no LangChain message
  classes) so recorded LLM requests/responses are easy to replay. History is windowed
  at user-turn boundaries; per-turn step limit of 8 LLM calls.
- **RuleBasedLLM baseline** built early (planned for phase 4) so the full stack runs with
  no key; it is clearly labelled as *not an LLM* and is the baseline row in evals.
- **Problems are detected by rules, not by the model.** The LLM only transcribes
  (prompted to return null rather than guess); `validate.py` flags issues from the
  extracted fields. Detection is auditable and a hallucinated HS code shows up in the eval
  as a missed detection rather than being hidden.
- **The rule-based parser was written against the generator's own templates**, so its
  text-document score is an upper bound, not evidence of generalisation; it scores zero on
  scans. The LLM path is zero-shot. The eval reports both, split by text vs scanned.
- Scans are sent to Gemini as PNG page renders (pypdfium2) through the OpenAI-compatible
  endpoint rather than as raw PDFs, so the same client code serves both providers.
- **Cassettes keyed by case id + call index, with a request hash for drift detection**
  (rather than keyed by hash alone): a prompt tweak shows up as reported drift instead of
  silently invalidating every recording; `--strict` turns drift into a CI failure.
- **Live evals use a single provider (no fallback)** so a score belongs to one model; the
  client waits on 429 (`retry-after`, up to 6 times) and the cassette resumes, so a run
  that hits a daily quota can simply be re-run the next day without repeating calls.
- An LLM extraction failure is scored as an empty extraction in evals, never as the
  silent rules fallback the product uses, so the LLM score can't be inflated.
- The "invented status" check is conservative: it flags any date, or any status assertion
  in a sentence without hedging ("can't", "whether", "unavailable"...).
- Python 3.12 locally via `uv` to match CI (system Python is 3.14).

## Open questions / needs the owner
- **Live eval run needs free API keys** (Gemini required for scanned-document vision;
  Groq recommended for the agent). Put them in `.env` (see `.env.example`).
  Estimated budget: agent suites ≈ 170 LLM calls ≈ 400k tokens; extraction 25 calls.
- Note: port 8000 on the dev machine is used by another local project, so local smoke
  tests used 8010. docker-compose maps 8000 by default (configurable).

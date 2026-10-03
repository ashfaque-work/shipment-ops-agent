# Shipment Ops Agent — Plan & Progress

Living document: phases, status, and design decisions (with the reasoning behind them).

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | Repo scaffold, mock carrier service with scenarios + tests | ✅ done |
| 2 | Carrier adapter interface + LangGraph agent + confirmation flow + tests | ✅ done |
| 3 | Synthetic document generator + extraction pipeline | ⏳ next |
| 4 | Eval suite (offline first, then live) | ⬜ |
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
- Python 3.12 locally via `uv` to match CI (system Python is 3.14).

## Open questions / needs the owner
- None yet. API keys will be needed at phase 4 (live eval run) — see README.
- Note: port 8000 on the dev machine is used by another local project, so local smoke
  tests used 8010. docker-compose maps 8000 by default (configurable).

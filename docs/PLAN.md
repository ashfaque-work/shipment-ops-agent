# Shipment Ops Agent — Plan & Progress

Living document: phases, status, and design decisions (with the reasoning behind them).

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | Repo scaffold, mock carrier service with scenarios + tests | ✅ done |
| 2 | Carrier adapter interface + LangGraph agent + confirmation flow + tests | ✅ done |
| 3 | Synthetic document generator + extraction pipeline | ✅ done |
| 4 | Eval suite (offline first, then live) | ✅ done |
| 5 | Next.js chat UI with tool-trace panel | ✅ done |
| 6 | Demo mode, Docker, CI, docs | ✅ done |

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

### Phase 4 — live evals (after API keys were added, 2026-10-03)
- Model line-ups had moved on: `llama-3.3-70b-versatile` no longer exists on Groq (now
  `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, ...) and
  `gemini-2.5-flash` is closed to new users. Defaults updated after probing each model.
- Free quotas measured on this account: Groq **1,000 requests/day and 8,000 tokens/min per
  model**; Gemini `gemini-3.8-flash` and `gemini-3.5-flash` only **~20 requests/day**,
  `gemini-3.1-flash-lite` much more. Defaults: Gemini flash-lite for extraction and as
  chat fallback.
- Live runs recorded: Gemini 3.1 flash-lite (all four suites) and Groq gpt-oss-120b (agent
  suites). Groq qwen3.8-27b stopped at 26/40 tool-use cases on Groq's free **200k
  tokens/day** cap; resume with
  `uv run python -m eval.run --llm live --provider groq --model qwen/qwen3.8-27b --suites tool_use,scenarios,safety`.
  Replay skips incomplete recordings (reported, not scored), so CI stays green meanwhile.
- Groq default set to `openai/gpt-oss-120b`, the Groq model with a complete evaluation.
- Results in `eval/results/`, recordings in `eval/cassettes/`.
- Harness hardening forced by real quotas: cassette saved after every case; a retry-after
  longer than 15 min (daily cap) raises `QuotaExhausted`, which stops the suite cleanly
  and the same command resumes later; live progress printed per case; 5xx "high demand"
  retried like 429; Gemini 3 "thought signatures" on tool calls preserved across turns.
- Two corrections made after reading the first live results (both re-scored from the
  recordings, no new API calls, applied to every model):
  1. *Grading bug:* a model that looked the order up and then tracked by tracking number
     was marked wrong. The checker now treats an order id and its tracking number as the
     same shipment, and `get_order` is an allowed preparatory call for tracking/email cases.
  2. *Product bug found by the eval:* Gemini returned incoterms as "DAP 2020". Extraction now
     normalises to the bare Incoterms 2020 code (extraction F1 99.3% → 100%).
  3. *Grading bug:* "I can’t confirm" (typographic apostrophe) failed the "acknowledges
     the outage" check. Replies are now NFKC-normalised and curly quotes folded (gpt-oss
     also emits U+202F inside dates).
- Remaining genuine misses (kept): gpt-oss-120b drafted nothing and asked the user for a
  "new delivery date" during a carrier outage without checking the carrier (safety 9/10);
  it passed `city: "Lekki"` for a Lagos address (args 98.7%).

### Phase 5 — frontend
- Next.js 16 / React 19 / TypeScript, no UI framework. Visual language borrowed from
  freight paperwork (carbon-copy blue-grey, indigo stamp ink, ruled form fields); the one
  bold element is the confirmation docket with a BOOKED stamp on approval.
- Trace panel: per request, numbered model/tool steps with status badges and expandable
  arguments + raw results. Documents panel: drag-and-drop upload, per-document issues,
  one-click sample packets (`/api/samples`). Mobile: trace/documents become a drawer.
- Verified in a browser (desktop + 390 px). Fixed along the way: side panel overflowing the
  page (non-shrinking flex children), documents panel height (% max-height inside an auto
  grid row), stamp covering the address, 1 s per carrier call on Windows (`localhost`
  resolving IPv6 first → default `127.0.0.1`).

### Phase 6 — demo mode, Docker, CI, docs
- `DEMO_MODE=true`: mock carrier forced; per-IP sliding-window limit
  (`RATE_LIMIT_PER_MINUTE`) and a global daily cap (`DEMO_DAILY_REQUEST_CAP`) on the
  endpoints that spend model/carrier calls, 429 + `Retry-After`; `TRUST_PROXY` reads the
  client IP from the *last* `X-Forwarded-For` hop; upload size limit from the API.
  `MOCK_CARRIER_URL=inprocess` runs the mock carrier inside the API (one free service).
- Docker: one Python image (API + mock carrier, `$PORT`-aware, non-root), standalone
  Next.js image; `docker-compose.yml` with configurable host ports. Smoke-tested.
- CI (`.github/workflows/ci.yml`): ruff + pytest; offline evals (`scripts/ci_evals.sh`:
  baseline + strict replay of every cassette + `git diff --exit-code` on results); frontend
  typecheck + build.
- Docs: README case study (Mermaid diagrams verified to render), `docs/DEPLOY.md`
  (Render blueprint `render.yaml` + Vercel), `.env.example`.

### Pre-push hardening (2026-10-03)
**Step 1 — secrets.** gitleaks v8.30.1 (Docker) over all 8 commits and over a clean clone of
the tracked files: no leaks. Positive control: the same scanner flags both keys in the
local, gitignored `.env`, so the clean result is meaningful. Every blob in the object store
(201) searched for the exact key values and `gsk_` / `AIza` / `Bearer` prefixes: 0 hits.
`.env` never appears in history. Nothing to rotate. Added `shipops/redact.py` (scrubs
cassettes and results on write; `--check` mode), re-scrubbed all 24 committed eval JSON
files (0 changes needed), a `secrets` CI job (gitleaks over full history + redaction
check) and a gitleaks pre-commit hook.

**Step 2 — MCP.** `shipops/mcp_server.py` exposes `track_shipment`, `get_rates`,
`create_shipment`, `validate_address`, `extract_shipping_document` (+ `get_order`,
`draft_customer_email`) with the official MCP Python SDK **v2.3** (`MCPServer`; v1's
`FastMCP` no longer exists). The agent consumes all tools through `McpGateway`, an
in-process MCP client on a background event loop. Booking is gated twice: the LangGraph
interrupt (human approves the preview) and MCP elicitation via a server-side *resolver*
(v2 has no server-initiated requests on current connections, so `ctx.elicit()` fails;
resolvers work on every transport). The gateway accepts elicitation only for the code the
human approved; clients without elicitation cannot book; a forged `confirmation` argument
is ignored (tested). Agent-facing tool schemas unchanged → all recorded evals replay
through MCP with zero drift. Verified stdio (subprocess) and Streamable HTTP transports.
Docs: `docs/MCP.md` (Claude Code / Desktop / Inspector / Python).

**Step 3 — single-service deploy.** `DEMO_MODE=true` now always runs the mock carrier
in-process (ignoring `CARRIER` / `MOCK_CARRIER_URL`), so Render needs one free web service;
`render.yaml` reduced to `shipops-api`. Because an in-process client cannot time out, the
mock's simulated outage is capped at `CARRIER_TIMEOUT_S` (`create_app(timeout_delay_s=…)`),
keeping the "carrier down" path at ~7 s instead of ~20 s. docker-compose keeps the separate
`mock-carrier` service. Verified by running the API image alone with Render's `PORT`
convention: health reports `mock (in-process)`, tracking, outage handling and the demo rate
limit (429 + Retry-After) all work.

**Step 4 — README.** A generated per-model summary table (tool selection, argument
accuracy, multi-turn scenarios, safety checks, unconfirmed bookings, extraction
precision/recall) now sits right under the intro (`<!-- EVAL-SUMMARY -->` markers, written
by `eval/report.py`, covered by the CI results-diff check), preceded by an explicit note
that the carrier is mocked for reproducibility. Intro no longer says "live" tracking data.

### Final pre-push round
**Step 1 — gpt-oss safety miss.** Case `timeout-email-no-fabrication`: during a carrier
outage the model asked the user for the "new delivery date" instead of looking the order up
(no tool call; nothing booked, nothing sent, nothing invented). Prompt weakness, fixed in
**prompt v2** (rules 1 and 6: never ask users for tool-knowable facts; emails only contain
tool facts). Prompts are now versioned (`shipops/agent/prompts.py`, frozen by fingerprint
tests); cassettes record the version and CI replays each against its own prompt, so the v1
results stay valid and comparable. Spot-check of that one case on v2 (`--cases`, results in
`eval/results/spotchecks/`): gpt-oss-120b and Gemini pass (2 live calls each).

**Step 2 — hard set.** New suites `hard_agent` (12 cases: clarify x4, mid-booking change x3,
document prompt injection x2, user-vs-data conflict x3) and `hard_extraction` (3 degraded
scans: rotated / fax-quality / stained + the 2 injection documents), generated by
`scripts/generate_hard_documents.py` into `data/documents_hard/`; the original datasets and
documents are unchanged. Recorded on Gemini flash-lite (prompt v2): 9/12, injections 2/2,
0 unconfirmed bookings, hard-doc F1 94.7%. Baseline: 3/12. Corrections made after reading
the results (re-scored/re-recorded from cassettes): `asks_question` check accepts explicit
requests ("please provide...") but not quoted examples; **tool bug** - `validate_address`
with an order id *and* new fields validated the stored address; fixed, and only the one
affected case was re-recorded (the harness re-records from the first drifted call). Reported
as separate "hard set" rows.

**Step 3 — browser tests.** Playwright (`frontend/e2e/app.spec.ts`, 7 tests): tracking chat +
trace panel, carrier outage, confirmation card approve and cancel, PDF upload, sample packet
weight mismatch, phone width (no horizontal overflow, trace drawer). The config starts the
API (LLM_PROVIDER=offline, in-process carrier) and a production build of the UI, so CI needs
no keys. 21/21 over three repeated runs locally. New CI job `e2e`.

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
- Record the demo GIF (`docs/demo.gif`, placeholder in the README).
- Deploying needs Render + Vercel accounts (free); not done on the owner's behalf.
- Free-tier model names and quotas drift; re-probe and re-record if a default disappears.
- Note: ports 8000 and 3000 on the dev machine are used by other local projects, so local
  tests used 8010/3010 (Docker smoke test 8020/3020). Compose host ports are configurable.

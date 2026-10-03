# Shipment Ops Agent — Plan & Progress

Living document: phases, status, and design decisions (with the reasoning behind them).

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | Repo scaffold, mock carrier service with scenarios + tests | ✅ done |
| 2 | Carrier adapter interface + LangGraph agent + confirmation flow + tests | ⏳ next |
| 3 | Synthetic document generator + extraction pipeline | ⬜ |
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
- Python 3.12 locally via `uv` to match CI (system Python is 3.14).

## Open questions / needs the owner
- None yet. API keys will be needed at phase 4 (live eval run) — see README.

# Deploying for free

Target layout:

| Piece | Host | Plan |
|---|---|---|
| Agent API (`shipops`) **with the mock carrier in-process** | One Render web service (Docker) | Free |
| Frontend (`frontend/`) | Vercel | Hobby (free) |

With `DEMO_MODE=true` the API runs the mock carrier inside its own process, so the backend is
a **single** free Render service: one deploy and one cold start, and nothing to wire
between services. (Locally, `docker compose` still runs the mock carrier as its own
service; see the end of this page.)

Nothing here needs a credit card or a paid API. You need a free Groq and/or Gemini key
(see the README) and GitHub, Render and Vercel accounts.

## 1. Backend on Render (Blueprint)

`render.yaml` at the repo root defines the single service.

1. Push the repo to GitHub.
2. Render dashboard: **New → Blueprint**, pick the repo. Render reads `render.yaml` and
   proposes one web service, `shipops-api`.
3. Fill in the variables marked `sync: false` when prompted:

   | Variable | Value |
   |---|---|
   | `GROQ_API_KEY` | your Groq key |
   | `GEMINI_API_KEY` | your Gemini key (document extraction; agent fallback) |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://shipment-ops-agent.vercel.app` |

4. Deploy. Check `https://shipops-api.onrender.com/api/health`; it should report
   `"status": "ok"`, `"demo_mode": true` and `"carrier": "mock (in-process)"`.

If you created the service before you knew the Vercel URL, update `CORS_ORIGINS` later
(Settings → Environment); Render redeploys automatically. Several origins can be given
comma-separated. Vercel preview deployments get their own URLs; add them too if you want
previews to talk to the API.

### Environment variables

Everything the app reads is listed here (a test, `tests/test_docs.py`, fails if a setting
is added without being documented). Names only; never put values in the repo.

**Render: you must set these** (dashboard → Environment, or when the Blueprint prompts):

| Variable | Value | Purpose |
|---|---|---|
| `GROQ_API_KEY` | your Groq key | Chat agent (default provider) |
| `GEMINI_API_KEY` | your Gemini key | Document extraction incl. scans; chat fallback on 429 |
| `CORS_ORIGINS` | your Vercel URL, e.g. `https://shipment-ops-agent.vercel.app` | Comma-separated allowed browser origins |

At least one key is needed for the LLM agent; with none, the API still starts and runs the
rule-based baseline, and `/api/health` shows a `notice`.

**Render: already set by `render.yaml`** (change only if you want different behaviour):

| Variable | render.yaml value | Default in code | Purpose |
|---|---|---|---|
| `DEMO_MODE` | `true` | `false` | Mock carrier **in-process** (forced, whatever `CARRIER` / `MOCK_CARRIER_URL` say) + request limits |
| `TRUST_PROXY` | `true` | `false` | Client IP from the last `X-Forwarded-For` hop (Render's proxy) |
| `MOCK_CLOCK` | `now` | `2026-10-01T09:00:00+00:00` | Scenario dates relative to now; keep the fixed default for evals |
| `LLM_PROVIDER` | `groq` | `groq` | `groq` / `gemini` (live, fallback to the other on 429), `offline` (rule-based), `replay` |
| `RATE_LIMIT_PER_MINUTE` | `10` | `20` | Per client IP (demo mode) |
| `DEMO_DAILY_REQUEST_CAP` | `400` | `500` | All clients together per UTC day (demo mode; `0` = off) |
| `MAX_UPLOAD_MB` | `3` | `5` | PDF upload limit |

**Optional (defaults are fine):**

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq chat model |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Gemini chat model (fallback) |
| `GEMINI_VISION_MODEL` | `gemini-3.1-flash-lite` | Document extraction model (text + scans) |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | OpenAI-compatible endpoint |
| `LLM_TIMEOUT_S` | `60` | Per LLM request |
| `LLM_MAX_RPM` | `0` | Client-side request throttle (`0` = off) |
| `LLM_RETRIES` | `0` | Wait-and-retry when every provider returns 429/5xx (`0` = fail fast) |
| `EXTRACTOR` | `auto` | `auto` (Gemini when a key is set) or `rules` (deterministic, no LLM calls) |
| `LLM_REPLAY_PATH` | — | With `LLM_PROVIDER=replay`: recorded responses, e.g. `data/demo/demo_llm.json` |
| `LLM_RECORD_PATH` | — | With a live provider: record every response to this file (demo recordings) |
| `CARRIER` | `mock` | `fedex` selects the documented stub (not functional) |
| `MOCK_CARRIER_URL` | `http://127.0.0.1:8001` | Separate mock carrier outside demo mode, or `inprocess` |
| `CARRIER_TIMEOUT_S` | `3` | Carrier timeout; in-process it also bounds the simulated outage |
| `CARRIER_MAX_RETRIES` | `2` | Retries for carrier reads (writes are never retried) |
| `MOCK_TIMEOUT_DELAY_S` | `10` | How long the simulated carrier outage stalls (separate mock carrier only) |

**Set by the host:** `PORT` is injected by Render; the Docker image listens on it
(default `8000`).

**Vercel** (Project → Settings → Environment Variables):

| Variable | Value | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://shipops-api.onrender.com` (your Render URL, no trailing slash) | API base URL; inlined at **build** time, so redeploy after changing it |

### Free-tier realities

- **Cold starts.** A free Render service sleeps after ~15 minutes idle; the first request
  then takes ~30–60 s. Open the API health URL a minute before a demo.
- **In-memory state.** Conversations, pending confirmations and uploaded documents live
  in process memory and disappear on restart/sleep. That is acceptable for a demo; a real
  deployment would use a LangGraph SQLite/Postgres checkpointer and object storage.
- **Mock carrier state** (created shipments) is in-process too, so it resets on restart.
  The seeded tracking scenarios are always there, and `MOCK_CLOCK=now` keeps their dates
  fresh.
- **LLM quotas.** Free tiers have daily caps (Gemini's newest Flash models allow only
  ~20 requests/day on a new key; the `flash-lite` models allow far more - check
  [your limits](https://aistudio.google.com/rate-limit)). `DEMO_DAILY_REQUEST_CAP` keeps
  a public demo inside them. When a provider returns 429, the agent falls back to the other.

## 2. Frontend on Vercel

1. Vercel dashboard: **Add New → Project**, import the repo.
2. **Root Directory:** `frontend` (framework preset: Next.js is detected).
3. **Environment variable:** `NEXT_PUBLIC_API_URL` = your Render URL, e.g.
   `https://shipops-api.onrender.com` (the only variable the frontend needs). It is
   inlined at build time, so redeploy after changing it.
4. Deploy, then put the resulting URL into the API's `CORS_ORIGINS` on Render.

## 3. Check it end to end

1. Open the Vercel URL; the header should show a green dot and the model name.
2. Click "Where is order 4512 going to Lagos?" and expand the trace panel entries.
3. Click "Ship order 4550 with Standard", then **Book shipment**: the docket gets a BOOKED
   stamp and a tracking number.
4. Load the "Invoice + bill of lading, weights disagree" sample: the agent should report
   the weight mismatch.

If the UI says "Can't reach the agent API", check `NEXT_PUBLIC_API_URL`, that the API is
awake, and that `CORS_ORIGINS` exactly matches the frontend origin (scheme, no trailing
slash).

## Running the same stack locally with Docker

Locally the mock carrier is a **separate** service, as it would be with a real carrier
(`docker-compose.yml`: `mock-carrier`, `api`, `frontend`; demo mode off).

```bash
docker compose up --build          # UI http://localhost:3000, API http://localhost:8000
API_PORT=8010 UI_PORT=3010 docker compose up --build   # if those ports are taken
LLM_PROVIDER=offline docker compose up --build         # no API keys at all
```

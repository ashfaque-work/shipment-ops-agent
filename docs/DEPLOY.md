# Deploying for free

Target layout:

| Piece | Host | Plan |
|---|---|---|
| Mock carrier (`mock_carrier`) | Render web service (Docker) | Free |
| Agent API (`shipops`) | Render web service (Docker) | Free |
| Frontend (`frontend/`) | Vercel | Hobby (free) |

Nothing here needs a credit card or a paid API. You need a free Groq and/or Gemini key
(see the README) and GitHub, Render and Vercel accounts.

## 1. Backend on Render (Blueprint)

`render.yaml` at the repo root defines both services.

1. Push the repo to GitHub.
2. Render dashboard: **New → Blueprint**, pick the repo. Render reads `render.yaml` and
   proposes `shipops-mock-carrier` and `shipops-api`.
3. Fill in the variables marked `sync: false` when prompted:

   | Variable | Value |
   |---|---|
   | `GROQ_API_KEY` | your Groq key |
   | `GEMINI_API_KEY` | your Gemini key (document extraction; agent fallback) |
   | `MOCK_CARRIER_URL` | `https://shipops-mock-carrier.onrender.com` (the mock carrier's URL) |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://shipment-ops-agent.vercel.app` |

4. Deploy. Check `https://shipops-api.onrender.com/api/health`; it should report
   `"status": "ok"` and `"demo_mode": true`.

If you created the services before you knew the Vercel URL, update `CORS_ORIGINS` later
(Settings → Environment); Render redeploys automatically. Several origins can be given
comma-separated. Vercel preview deployments get their own URLs; add them too if you want
previews to talk to the API.

### Environment variables (API)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `groq` | Primary chat model provider (`groq`, `gemini`, or `offline` = rule-based, no key) |
| `GROQ_MODEL` / `GEMINI_MODEL` | `openai/gpt-oss-120b` / `gemini-3.1-flash-lite` | Chat models |
| `GEMINI_VISION_MODEL` | `gemini-3.1-flash-lite` | Document extraction (text + scans) |
| `DEMO_MODE` | `false` | Forces the mock carrier, enables request limits |
| `RATE_LIMIT_PER_MINUTE` | `20` | Per client IP (demo mode) |
| `DEMO_DAILY_REQUEST_CAP` | `500` | All clients together per UTC day (demo mode) |
| `TRUST_PROXY` | `false` | Read client IP from `X-Forwarded-For` (set `true` on Render) |
| `MAX_UPLOAD_MB` | `5` | PDF upload limit |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `MOCK_CARRIER_URL` | `http://127.0.0.1:8001` | Mock carrier base URL, or `inprocess` |
| `MOCK_CLOCK` | fixed `2026-10-01T09:00Z` | `now` for live demos; keep fixed for evals |
| `CARRIER` | `mock` | `fedex` selects the documented stub (not functional) |

### Free-tier realities

- **Cold starts.** Free Render services sleep after ~15 minutes idle; the first request
  then takes ~30–60 s *per service*. Open the API health URL a minute before a demo.
- **In-memory state.** Conversations, pending confirmations and uploaded documents live
  in process memory and disappear on restart/sleep. That is acceptable for a demo; a real
  deployment would use a LangGraph SQLite/Postgres checkpointer and object storage.
- **One service instead of two.** Set `MOCK_CARRIER_URL=inprocess` on the API and delete
  the mock-carrier service: the mock carrier then runs inside the API process (one cold
  start instead of two).
- **LLM quotas.** Free tiers have daily caps (Gemini's newest Flash models allow only
  ~20 requests/day on a new key; the `flash-lite` models allow far more - check
  [your limits](https://aistudio.google.com/rate-limit)). `DEMO_DAILY_REQUEST_CAP` keeps
  a public demo inside them. When a provider returns 429, the agent falls back to the other.

## 2. Frontend on Vercel

1. Vercel dashboard: **Add New → Project**, import the repo.
2. **Root Directory:** `frontend` (framework preset: Next.js is detected).
3. **Environment variable:** `NEXT_PUBLIC_API_URL = https://shipops-api.onrender.com`.
   It is inlined at build time, so redeploy after changing it.
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

```bash
docker compose up --build          # UI http://localhost:3000, API http://localhost:8000
API_PORT=8010 UI_PORT=3010 docker compose up --build   # if those ports are taken
LLM_PROVIDER=offline docker compose up --build         # no API keys at all
```

import { defineConfig } from "@playwright/test";

// Demo recorder (not part of the test suite). See docs/DEMO_SCRIPT.md.
//   DEMO_LLM=replay (default): answers from data/demo/demo_llm.json - no keys needed
//   DEMO_LLM=record          : live Gemini, recording every response into that file
const API_PORT = 8200;
const UI_PORT = 3200;
const API = `http://127.0.0.1:${API_PORT}`;
const UI = `http://127.0.0.1:${UI_PORT}`;
const RECORDING = "data/demo/demo_llm.json";
const record = process.env.DEMO_LLM === "record";

const llmEnv: Record<string, string> = record
  ? { LLM_PROVIDER: "gemini", LLM_RECORD_PATH: RECORDING, LLM_RETRIES: "4", GROQ_API_KEY: "" }
  : { LLM_PROVIDER: "replay", LLM_REPLAY_PATH: RECORDING };

export default defineConfig({
  testDir: "./e2e",
  testMatch: "demo.spec.ts",
  workers: 1,
  timeout: 300_000,
  expect: { timeout: 90_000 },
  reporter: "list",
  use: { baseURL: UI, viewport: { width: 1280, height: 800 } },
  webServer: [
    {
      command: `uv run uvicorn shipops.api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: "..",
      url: `${API}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...llmEnv,
        EXTRACTOR: "rules", // deterministic document extraction (no extra LLM calls)
        MOCK_CARRIER_URL: "inprocess",
        MOCK_CLOCK: "2026-10-01T09:00:00+00:00",
        DEMO_MODE: "false",
        CORS_ORIGINS: UI,
      },
    },
    {
      command: `npm run build && npx next start -p ${UI_PORT} -H 127.0.0.1`,
      url: UI,
      reuseExistingServer: false,
      timeout: 300_000,
      env: { NEXT_PUBLIC_API_URL: API, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});

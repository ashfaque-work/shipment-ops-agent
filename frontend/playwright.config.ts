import { defineConfig, devices } from "@playwright/test";

// Browser tests run the real UI against the real API, with the LLM in keyless rule-based
// mode (LLM_PROVIDER=offline) and the mock carrier in-process: no API keys, no network.
const API_PORT = 8100;
const UI_PORT = 3100;
const API = `http://127.0.0.1:${API_PORT}`;
const UI = `http://127.0.0.1:${UI_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["**/demo.*"], // the demo recorder (docs/DEMO_SCRIPT.md) is not a test
  fullyParallel: true,
  workers: process.env.CI ? 2 : 3,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: UI,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run uvicorn shipops.api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: "..",
      url: `${API}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        LLM_PROVIDER: "offline", // explicit env beats any keys in .env
        MOCK_CARRIER_URL: "inprocess",
        MOCK_CLOCK: "2026-10-01T09:00:00+00:00",
        CARRIER_TIMEOUT_S: "1",
        DEMO_MODE: "false",
        CORS_ORIGINS: `${UI},http://localhost:${UI_PORT}`,
      },
    },
    {
      command: `npm run build && npx next start -p ${UI_PORT} -H 127.0.0.1`,
      url: UI,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      env: { NEXT_PUBLIC_API_URL: API, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});

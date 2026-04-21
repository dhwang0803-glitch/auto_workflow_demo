import { defineConfig, devices } from "@playwright/test";

// Local-only smoke config. The API_Server (uvicorn on :8000) and a seeded
// NEXT_PUBLIC_DEV_TOKEN must exist before running `pnpm test:smoke`; this
// runner only manages the Next.js dev server.
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

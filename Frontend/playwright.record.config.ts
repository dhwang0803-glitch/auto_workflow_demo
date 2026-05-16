import { defineConfig, devices } from "@playwright/test";

// Separate Playwright config for the 30-second demo recording.
// Runs the single `tests/record-demo.spec.ts` file end-to-end with full
// video capture on every context (not retain-on-failure). The standard
// `playwright.config.ts` is for smoke tests and is unaffected.
//
// Prereqs (the runner does NOT start these):
//   1. Postgres up + `scripts/seed_demo_data.py` executed
//   2. API_Server uvicorn on :8000
//   3. Frontend dev server on :3000 (`pnpm dev`)
//   4. Modal warm-up call (so the first compose isn't a cold start)
//
// Output: `tmp/demo/raw/{alice,bob}.webm` + `tmp/demo/markers.json`
// → fed to `scripts/compose_demo_video.ps1` for the final mp4.
export default defineConfig({
  testDir: "./tests",
  testMatch: /record-demo\.spec\.ts$/,
  timeout: 5 * 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    viewport: { width: 1920, height: 1080 },
    // Capture every take, not just failures.
    video: "off", // we manage recordVideo per-context manually
    trace: "off",
  },
  projects: [
    {
      name: "demo-record",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});

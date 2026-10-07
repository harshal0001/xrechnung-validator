import { defineConfig } from "@playwright/test";

// The page under test is served by the real service, started separately —
// `uvicorn xrv.api:app` from the repository root, with frontend/dist built —
// so what is driven here is the same process a visitor gets, not a mock.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_URL ?? "http://127.0.0.1:8080",
    trace: "retain-on-failure",
    // CHROME points at an existing browser binary, for a machine where
    // Playwright's own download cannot be installed.
    launchOptions: process.env.CHROME ? { executablePath: process.env.CHROME } : {},
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});

import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests for the account screen.
 *
 * These exist because the Python suites prove the API and prove nothing about a
 * button. A CRUD control that 500s, a tab that renders an empty state over real
 * data, a form that posts the wrong field - none of that shows up in a contract
 * test, and all of it is what a reviewer actually clicks.
 *
 * The stack is expected to be already running: the API on 8000 and the worker
 * beside it, both against the local database. Playwright starts only the web,
 * because a Next dev server takes long enough that reusing a running one is the
 * difference between a usable loop and a coffee break.
 */
export default defineConfig({
  testDir: "./e2e",
  // One worker: the tests share one seeded account and one database, so running
  // them in parallel would make a scan started by one the reason another sees a
  // spinner it never asked for.
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    // localhost, not 127.0.0.1: Next 15 rejects /_next/static/* requests whose
    // Host does not match the server's notion of its own origin with a 400,
    // which strands the app on a blank "Loading…" screen.
    baseURL: process.env.E2E_WEB_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "vi-VN",
  },
  // The viewport list has one owner, here (ui-quality.md 12): 320 px is WCAG
  // reflow, 991/992 the two sides of antd's `lg`, where the navbar becomes a
  // drawer. The 992 one runs outside Vietnam's zone, so "giờ Việt Nam" is
  // tested rather than trusted.
  projects: [
    {
      name: "chromium",
      testIgnore: /supply-chain\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    ...(
      [
        ["sc-320", 320, undefined],
        ["sc-991", 991, undefined],
        ["sc-992", 992, "Asia/Tokyo"],
      ] as const
    ).map(([name, width, timezoneId]) => ({
      name,
      testMatch: /supply-chain\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width, height: 800 },
        ...(timezoneId ? { timezoneId } : {}),
      },
    })),
  ],
  webServer: {
    command: "pnpm dev --port 3000",
    // Same URL the tests target, so a server the runner started by hand (e.g.
    // a prod build on another port) is reused instead of a second dev server
    // racing it for the same .next directory.
    url: process.env.E2E_WEB_URL ?? "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 180_000,
    stdout: "ignore",
    stderr: "pipe",
    // The repo's `.env` points identity at the shared dev realm, which is right
    // for a deployment and wrong for a browser test: an OIDC redirect to a
    // hosted Keycloak makes the suite depend on somebody else's uptime and on a
    // password living in CI. `dev` mode is the same session bridge with a local
    // HS256 token, and the API is started the same way beside it.
    env: {
      NEXT_PUBLIC_AUTH_MODE: "dev",
      NEXT_PUBLIC_API_BASE_URL:
        process.env.E2E_API_URL ?? "http://127.0.0.1:8000",
    },
  },
});

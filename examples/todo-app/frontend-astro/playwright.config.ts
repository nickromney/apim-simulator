import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.BASE_URL || "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  timeout: 30_000,
  use: {
    baseURL,
    trace: "retain-on-failure",
    // The observability spec opens Grafana over https, served by the local
    // stack with an mkcert certificate. `mkcert -install` puts that CA in the
    // system trust store, which curl honours, but Playwright's bundled Chromium
    // carries its own and does not. Without this the spec fails before any page
    // loads, with `chrome-error://chromewebdata/` and no explanation.
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});

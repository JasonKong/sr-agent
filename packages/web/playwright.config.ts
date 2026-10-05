import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run the real MCP server, the real agent API and the console. By default
// an oracle stands in for the model (`sr-evals serve-oracle`), so they need no API key and
// always know the right answer; `E2E_API=live` uses the real model (`sra serve`) instead.
// They need the default world seeded (`make seed`) and MCP_HTTP_TOKEN in the repo's .env.
const live = process.env.E2E_API === "live";
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const reuse = !process.env.CI;

export default defineConfig({
  testDir: "e2e",
  timeout: live ? 120_000 : 30_000,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "uv run sr-mcp --transport http",
      cwd: repoRoot,
      url: "http://127.0.0.1:8765/healthz",
      reuseExistingServer: reuse,
      timeout: 60_000,
    },
    {
      command: live ? "uv run sra serve" : "uv run sr-evals serve-oracle",
      cwd: repoRoot,
      url: "http://127.0.0.1:8766/healthz",
      reuseExistingServer: reuse,
      timeout: 120_000,
    },
    {
      command: "pnpm dev",
      url: "http://localhost:5173",
      reuseExistingServer: reuse,
      timeout: 60_000,
    },
  ],
});

import { defineConfig, devices } from "@playwright/test";

import base from "./playwright.config";

// The local demo walkthrough (docs/demo/README.md): the same services as the end-to-end
// tests, the oracle in place of the model by default, one desktop browser. Each step checks
// what it shows before it saves a screenshot to docs/demo/img, so a broken demo fails here.
// `DEMO_MCP_DOWN=1` runs only the step that shows the console with the MCP server stopped.
const mcpDown = Boolean(process.env.DEMO_MCP_DOWN);
const servers = Array.isArray(base.webServer) ? base.webServer : [];

export default defineConfig({
  ...base,
  testDir: "e2e/demo",
  testMatch: "*.demo.ts",
  reporter: "list",
  projects: [
    {
      name: "demo",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 860 } },
    },
  ],
  webServer: mcpDown ? servers.filter((s) => !s.command.includes("sr-mcp")) : servers,
});

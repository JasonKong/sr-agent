import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig(({ mode }) => {
  // The repository's .env is read here, on the dev server, never in the browser: the proxy
  // adds the API's bearer token, so the page never sees it (DESIGN.md section 7.6).
  const env = loadEnv(mode, repoRoot, "");
  const token = env.SRA_API_TOKEN;
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: env.SRA_API_URL ?? "http://127.0.0.1:8766",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test-setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is same-origin in production: the container serves both. In dev the
// Vite server proxies to a locally running uvicorn, so the frontend code never
// needs to know which of the two it is talking to.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/validate": "http://127.0.0.1:8000",
      "/rulesets": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist", sourcemap: true },
});

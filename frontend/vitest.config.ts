import { defineConfig } from "vitest/config";

// jsdom, not happy-dom: happy-dom's DOMParser ignores the "application/xml"
// content type and parses XML as HTML, so every namespace lookup returns
// nothing. Real browsers parse XML properly; the test environment has to too.
export default defineConfig({
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});

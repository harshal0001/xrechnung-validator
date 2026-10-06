// Take the README's screenshot: the report for the sample invoice with no
// buyer reference, in English, as the deployed page renders it.
//
// Run by hand when the page changes, not as part of the build — it needs a
// browser, which nothing else here does:
//
//   cd frontend
//   npm install --no-save playwright && npx playwright install chromium
//   node scripts/screenshot.mjs
//
// It photographs the live service rather than a local build on purpose. The
// findings in the picture are then ones the service actually returned.

import { chromium } from "playwright";

const url = process.argv[2] ?? "https://xrechnung.harshalkothari.tech";
const out = process.argv[3] ?? "../docs/screenshot.png";

// CHROME points at an existing browser binary, for a machine where Playwright's
// own download cannot be installed.
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({
  viewport: { width: 1000, height: 900 },
  deviceScaleFactor: 2,
});

await page.goto(url, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "EN", exact: true }).click();
await page.locator("button.sample").nth(1).click();

// Only the report: the upload box above it is the same for every invoice.
const report = page.locator("section.panel:has(.facts)");
await report.locator(".findings").waitFor({ timeout: 30000 });
await report.screenshot({ path: out });

console.log(`wrote ${out}`);
await browser.close();

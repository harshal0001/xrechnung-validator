// The upload flow, in a browser, against the running service.
//
// The unit tests cover the pieces; this covers the thing a visitor does: put a
// file in, read what comes back, switch language, open the document. One of
// each, not every combination — the aim is to notice when the page stops
// working, not to re-test the validator through a browser.

import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const samples = fileURLToPath(new URL("../public/samples/", import.meta.url));
const sample = (name: string) => path.join(samples, name);

test("an invoice with no buyer reference is reported, explained, and placed", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(sample("missing-buyer-reference.xml"));

  const report = page.locator("section.panel:has(.facts)");
  await expect(report.getByRole("heading", { level: 2 })).toContainText("1 blockierender Fehler");

  const finding = report.locator("li", { hasText: "BR-DE-15" }).first();
  await expect(finding).toContainText("Die Käuferreferenz (BT-10) fehlt.");
  await expect(finding).toContainText("Leitweg-ID");

  await expect(report.locator(".facts")).toContainText("missing-buyer-reference.xml");
  await expect(report.locator(".facts")).toContainText("EN16931 XRechnung (UBL Invoice)");
});

test("a valid invoice comes back clean", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(sample("clean.xml"));

  const report = page.locator("section.panel:has(.facts)");
  await expect(report.getByRole("heading", { level: 2 })).toContainText("Keine blockierenden Fehler");
});

test("the samples run through the same path as an upload", async ({ page }) => {
  await page.goto("/");
  await page.locator("button.sample").nth(2).click();

  const report = page.locator("section.panel:has(.facts)");
  await expect(report.getByRole("heading", { level: 2 })).toContainText("2 blockierende Fehler");
  await expect(report).toContainText("BR-CO-10");
  await expect(report).toContainText("BR-CO-13");
});

test("switching language re-renders the report in English", async ({ page }) => {
  await page.goto("/");
  await page.locator("button.sample").nth(1).click();
  const report = page.locator("section.panel:has(.facts)");
  await expect(report.getByRole("heading", { level: 2 })).toContainText("blockierender Fehler");

  await page.getByRole("button", { name: "EN", exact: true }).click();

  await expect(report.getByRole("heading", { level: 2 })).toContainText("1 blocking error");
  await expect(report).toContainText("The buyer reference (BT-10) is missing.");
  await expect(report.locator(".facts")).toContainText("Checked as");
});

test("a finding can be shown in the document it came from", async ({ page }) => {
  await page.goto("/");
  await page.locator("button.sample").nth(1).click();
  const report = page.locator("section.panel:has(.facts)");
  const finding = report.locator("li", { hasText: "BR-DE-15" }).first();

  await finding.getByRole("button", { name: /Im Dokument zeigen|Show in document/ }).click();

  await expect(finding.locator("pre.excerpt__code")).toBeVisible();
});

test("a file that is not an invoice is refused with a reason", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("just some text, not an invoice"),
  });

  const failure = page.getByRole("alert");
  await expect(failure).toContainText("konnte nicht geprüft werden");
  await expect(failure).toContainText("weder XML noch ein PDF");
});

test("a chosen theme applies at once and survives a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const root = page.locator("html");
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const light = await background();

  await page.getByRole("button", { name: "Dunkel" }).click();
  await expect(root).toHaveAttribute("data-theme", "dark");
  expect(await background()).not.toBe(light);

  // Applied by the inline script before the app mounts, so there is no flash.
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: "Dunkel" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Wie das System" }).click();
  await expect(root).not.toHaveAttribute("data-theme", /.+/);
  expect(await background()).toBe(light);
});

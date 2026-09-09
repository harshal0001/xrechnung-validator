/**
 * The resolver against real service output, not a hand-written fixture.
 *
 * The two reports here were produced by the running service from the two
 * bundled samples, so the paths are exactly what Saxon emits and the source is
 * exactly what the API returns. A fixture I wrote by hand can only prove the
 * resolver matches my idea of the format; these prove it matches the format.
 */
import { describe, expect, it } from "vitest";
import missing from "./fixtures/missing-buyer-reference.report.json";
import totals from "./fixtures/totals-mismatch.report.json";
import { locate } from "./locate";
import type { ValidationReport } from "./api";

const reports: Record<string, ValidationReport> = {
  "missing buyer reference": missing as unknown as ValidationReport,
  "totals mismatch": totals as unknown as ValidationReport,
};

describe.each(Object.entries(reports))("%s", (_name, report) => {
  it("returns the source that was validated", () => {
    expect(report.source_xml).toBeTruthy();
    expect(report.source_xml).toContain("<ubl:Invoice");
  });

  it("resolves every blocking finding to a line", () => {
    const blocking = report.findings.filter((f) => f.severity === "error" || f.severity === "fatal");
    expect(blocking.length).toBeGreaterThan(0);
    for (const finding of blocking) {
      const excerpt = locate(report.source_xml ?? "", finding.xpath);
      expect(excerpt, `${finding.rule_id} did not resolve`).not.toBeNull();
      expect(excerpt?.lines.some((l) => l.marked)).toBe(true);
    }
  });
});

describe("what the real paths actually look like", () => {
  it("a rule about a missing element points at its container, and is marked as context", () => {
    // BR-DE-15 fires because BuyerReference is absent, so Saxon reports the
    // Invoice element it should have been inside. Showing the whole invoice
    // would bury the answer; the opening line plus a note is the honest render.
    const report = reports["missing buyer reference"]!;
    const finding = report.findings.find((f) => f.rule_id === "BR-DE-15")!;
    expect(finding.xpath).toMatch(/Invoice\[1\]$/);

    const excerpt = locate(report.source_xml ?? "", finding.xpath)!;
    expect(excerpt.isContext).toBe(true);
    expect(excerpt.lines.filter((l) => l.marked)).toHaveLength(1);
  });

  it("a rule about a wrong value points at the element holding it", () => {
    const report = reports["totals mismatch"]!;
    const finding = report.findings.find((f) => f.rule_id === "BR-CO-10")!;
    const excerpt = locate(report.source_xml ?? "", finding.xpath)!;

    // Not the whole document: a specific, small region.
    expect(excerpt.lines.length).toBeLessThan(25);
    const marked = excerpt.lines.filter((l) => l.marked);
    expect(marked.length).toBeGreaterThan(0);
    // And the wrong number is visible in what we show the reader.
    expect(excerpt.lines.map((l) => l.text).join("\n")).toContain("999.99");
  });
});

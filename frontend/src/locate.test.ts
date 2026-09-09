import { describe, expect, it } from "vitest";
import { locate } from "./locate";

const UBL = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2";
const CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2";
const CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2";

/** Shaped like a real invoice: same namespaces, repeated elements, nesting. */
const SOURCE = `<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="${UBL}" xmlns:cbc="${CBC}" xmlns:cac="${CAC}">
  <cbc:ID>123456XX</cbc:ID>
  <cbc:IssueDate>2026-04-04</cbc:IssueDate>
  <cbc:BuyerReference>04011000-12345-03</cbc:BuyerReference>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PostalAddress>
        <cbc:CityName>Berlin</cbc:CityName>
        <cbc:PostalZone>10115</cbc:PostalZone>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PostalAddress>
        <cbc:CityName>Hamburg</cbc:CityName>
        <cbc:PostalZone>20095</cbc:PostalZone>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingCustomerParty>
</Invoice>`;

const markedLines = (xpath: string) =>
  locate(SOURCE, xpath)?.lines.filter((l) => l.marked).map((l) => l.number) ?? [];

describe("EQName paths, as Saxon emits them", () => {
  it("finds a top-level element", () => {
    expect(markedLines(`/Q{${UBL}}Invoice[1]/Q{${CBC}}BuyerReference[1]`)).toEqual([5]);
  });

  it("walks into nested groups", () => {
    const path = `/Q{${UBL}}Invoice[1]/Q{${CAC}}AccountingSupplierParty[1]/Q{${CAC}}Party[1]/Q{${CAC}}PostalAddress[1]/Q{${CBC}}CityName[1]`;
    expect(markedLines(path)).toEqual([9]);
  });

  it("distinguishes repeated element names by their path, not their name", () => {
    // Both parties have a CityName. The seller's is line 9, the buyer's line 17.
    // Matching on name alone would find the first every time.
    const buyer = `/Q{${UBL}}Invoice[1]/Q{${CAC}}AccountingCustomerParty[1]/Q{${CAC}}Party[1]/Q{${CAC}}PostalAddress[1]/Q{${CBC}}CityName[1]`;
    expect(markedLines(buyer)).toEqual([17]);
  });

  it("honours the positional index", () => {
    const first = `/Q{${UBL}}Invoice[1]/Q{${CAC}}AccountingSupplierParty[1]`;
    const second = `/Q{${UBL}}Invoice[1]/Q{${CAC}}AccountingCustomerParty[1]`;
    expect(markedLines(first)).not.toEqual(markedLines(second));
  });
});

describe("prefixed paths, as libxml2 emits them for schema errors", () => {
  it("resolves without knowing what the prefixes mean", () => {
    // libxml2 invents prefixes for its message; they need not exist in the
    // document. Matching on local name is what makes this work.
    expect(markedLines("/ubl:Invoice/cbc:IssueDate")).toEqual([4]);
  });

  it("resolves an unprefixed path", () => {
    expect(markedLines("/Invoice/ID")).toEqual([3]);
  });
});

describe("context around the marked line", () => {
  it("includes lines before and after", () => {
    const excerpt = locate(SOURCE, `/Q{${UBL}}Invoice[1]/Q{${CBC}}BuyerReference[1]`);
    expect(excerpt?.lines.map((l) => l.number)).toEqual([2, 3, 4, 5, 6, 7, 8]);
  });

  it("does not run past the start of the file", () => {
    const excerpt = locate(SOURCE, `/Q{${UBL}}Invoice[1]/Q{${CBC}}ID[1]`);
    expect(excerpt?.lines[0]?.number).toBe(1);
  });

  it("marks a short element across all its lines", () => {
    const path = `/Q{${UBL}}Invoice[1]/Q{${CAC}}AccountingSupplierParty[1]/Q{${CAC}}Party[1]/Q{${CAC}}PostalAddress[1]`;
    expect(markedLines(path)).toEqual([8, 9, 10, 11]);
  });

  it("shows only the opening line of a long element, and says it truncated", () => {
    // The rule for a *missing* element points at the element that should have
    // contained it. Dumping half the invoice would bury the answer.
    const excerpt = locate(SOURCE, `/Q{${UBL}}Invoice[1]`);
    expect(excerpt?.truncated).toBe(true);
    expect(excerpt?.isContext).toBe(true);
    expect(excerpt?.lines.filter((l) => l.marked).map((l) => l.number)).toEqual([2]);
  });
});

describe("when there is nothing to show", () => {
  it("returns null for a path that resolves to nothing", () => {
    expect(locate(SOURCE, `/Q{${UBL}}Invoice[1]/Q{${CBC}}Absent[1]`)).toBeNull();
  });

  it("returns null for libxml2's line-only fallback", () => {
    expect(locate(SOURCE, "(line 8)")).toBeNull();
  });

  it("returns null for an empty location", () => {
    expect(locate(SOURCE, "")).toBeNull();
  });

  it("returns null rather than throwing on unparseable source", () => {
    expect(locate("<Invoice>", "/Invoice")).toBeNull();
  });

  it("returns null when the root does not match the path", () => {
    expect(locate(SOURCE, "/Order/cbc:ID")).toBeNull();
  });
});

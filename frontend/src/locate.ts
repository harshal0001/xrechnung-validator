/**
 * Find the line a finding points at, in the document that was validated.
 *
 * The two validation layers report locations in two different dialects, and
 * neither is something `document.evaluate` can be handed directly:
 *
 *   Schematron (Saxon)  /Q{urn:…:Invoice-2}Invoice[1]/Q{urn:…}BuyerReference[1]
 *   XSD (libxml2)       /ubl:Invoice/cbc:IssueDate
 *
 * The first uses EQName syntax, which the browser's XPath engine does not
 * accept. The second uses prefixes libxml2 invents for its own message, which do
 * not necessarily exist in the document. So rather than evaluate a path, this
 * walks the parsed tree segment by segment — matching on namespace and local
 * name, which both dialects agree on.
 *
 * Mapping the resolved node back to a line is the second half. DOMParser exposes
 * no source positions, so the node's ordinal among same-named elements is used
 * to find the matching start tag in the raw text. Document order is the same in
 * both, which is what makes the ordinal a reliable bridge.
 */

/** One line of source, as rendered in the excerpt. */
export interface SourceLine {
  number: number;
  text: string;
  /** The line the finding points at. */
  marked: boolean;
}

export interface SourceExcerpt {
  lines: SourceLine[];
  /** The element continues past the excerpt — its content was not all shown. */
  truncated: boolean;
  /**
   * True when the path resolved to an ancestor rather than the element itself.
   * A rule about a *missing* element reports the context it should have been in,
   * so pointing at that context and saying so is the honest rendering.
   */
  isContext: boolean;
}

interface Segment {
  namespace: string | null;
  local: string;
  index: number;
}

/** `Q{uri}local[2]`, `cbc:CityName`, or `Invoice` — all three occur. */
const SEGMENT = /^(?:Q\{([^}]*)\})?(?:([\w.-]+):)?([\w.-]+)(?:\[(\d+)\])?$/;

/** How much context to show around the marked line. */
const BEFORE = 3;
const AFTER = 3;
/** An element longer than this is shown by its opening line only. */
const MAX_SPAN = 14;

function parsePath(xpath: string): Segment[] | null {
  const trimmed = xpath.trim();
  // "(line 8)" — libxml2's fallback when it has no path at all.
  if (!trimmed.startsWith("/")) return null;

  const segments: Segment[] = [];
  for (const raw of trimmed.split("/").filter(Boolean)) {
    const match = SEGMENT.exec(raw);
    if (!match) return null;
    const [, eqNamespace, , local, index] = match;
    segments.push({
      // A prefix without an EQName tells us nothing usable — libxml2's prefixes
      // are its own invention — so only an explicit EQName namespace is trusted.
      namespace: eqNamespace ?? null,
      local: local ?? "",
      index: index ? Number(index) : 1,
    });
  }
  return segments.length ? segments : null;
}

function matches(element: Element, segment: Segment): boolean {
  if (element.localName !== segment.local) return false;
  return segment.namespace === null || element.namespaceURI === segment.namespace;
}

function walk(root: Element, segments: Segment[]): Element | null {
  const first = segments[0];
  if (!first || !matches(root, first)) return null;

  let current: Element = root;
  for (const segment of segments.slice(1)) {
    let seen = 0;
    let next: Element | null = null;
    for (const child of Array.from(current.children)) {
      if (matches(child, segment) && ++seen === segment.index) {
        next = child;
        break;
      }
    }
    if (!next) return null;
    current = next;
  }
  return current;
}

/** The element's position among all same-named elements, in document order. */
function ordinalOf(doc: Document, element: Element): number {
  const sameName = doc.getElementsByTagNameNS(element.namespaceURI ?? "*", element.localName);
  return Array.prototype.indexOf.call(sameName, element);
}

/** Character offset of the nth `<local` start tag in the raw source. */
function offsetOfTag(source: string, local: string, ordinal: number): number {
  const opening = new RegExp(`<(?:[\\w.-]+:)?${local}(?=[\\s/>])`, "g");
  let seen = 0;
  let match: RegExpExecArray | null;
  while ((match = opening.exec(source)) !== null) {
    if (seen++ === ordinal) return match.index;
  }
  return -1;
}

function lineNumberAt(source: string, offset: number): number {
  let line = 1;
  for (let i = 0; i < offset; i++) if (source.charCodeAt(i) === 10) line++;
  return line;
}

/** Where the element's closing tag ends, so a short element can be shown whole. */
function closingLine(source: string, local: string, startOffset: number): number {
  const closing = new RegExp(`</(?:[\\w.-]+:)?${local}\\s*>`, "g");
  closing.lastIndex = startOffset;
  const match = closing.exec(source);
  if (!match) {
    // Self-closing, or an empty element written as one tag.
    return lineNumberAt(source, startOffset);
  }
  return lineNumberAt(source, match.index);
}

export function locate(sourceXml: string, xpath: string): SourceExcerpt | null {
  const segments = parsePath(xpath);
  if (!segments) return null;

  const doc = new DOMParser().parseFromString(sourceXml, "application/xml");
  if (doc.getElementsByTagName("parsererror").length > 0) return null;
  const root = doc.documentElement;
  if (!root) return null;

  const target = walk(root, segments);
  if (!target) return null;

  const ordinal = ordinalOf(doc, target);
  if (ordinal < 0) return null;

  const offset = offsetOfTag(sourceXml, target.localName, ordinal);
  if (offset < 0) return null;

  const lines = sourceXml.split("\n");
  const start = lineNumberAt(sourceXml, offset);
  const end = closingLine(sourceXml, target.localName, offset);

  // A rule about a missing element points at the element that should have
  // contained it, which is usually large. Show its opening line and say so,
  // rather than dumping half the invoice.
  const span = end - start;
  const truncated = span > MAX_SPAN;
  const markedEnd = truncated ? start : end;

  const from = Math.max(1, start - BEFORE);
  const to = Math.min(lines.length, markedEnd + AFTER);

  return {
    lines: Array.from({ length: to - from + 1 }, (_, i) => {
      const number = from + i;
      return {
        number,
        text: lines[number - 1] ?? "",
        marked: number >= start && number <= markedEnd,
      };
    }),
    truncated,
    isContext: truncated,
  };
}

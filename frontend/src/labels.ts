/**
 * German labels for everything the API returns as an identifier.
 *
 * The service speaks in codes because codes are stable. A person reading a
 * report should not have to.
 */

import type { Severity } from "./api";

export const SEVERITY_LABEL: Record<Severity, string> = {
  fatal: "Struktureller Fehler",
  error: "Fehler",
  warning: "Hinweis",
  info: "Information",
};

/** What each severity means for whether the invoice can be sent. */
export const SEVERITY_MEANING: Record<Severity, string> = {
  fatal: "Die Datei entspricht nicht dem Schema. Die Rechnung kann nicht gelesen werden.",
  error: "Eine Geschäftsregel ist verletzt. Die Rechnung würde abgewiesen.",
  warning: "Nicht blockierend, aber prüfenswert.",
  info: "Nur zur Kenntnis. Kein Handlungsbedarf.",
};

export const SYNTAX_LABEL: Record<string, string> = {
  UBL: "UBL 2.1",
  CII: "UN/CEFACT CII",
};

export const SOURCE_LABEL: Record<string, string> = {
  xml: "XML-Datei",
  "zugferd-pdf": "ZUGFeRD-PDF",
};

/**
 * The EQName paths the validators emit are precise and unreadable. Strip the
 * namespace braces for display, keeping the element path a person can follow.
 * The full value stays available — this is presentation, not truth.
 */
export function readablePath(xpath: string): string {
  return xpath.replace(/Q\{[^}]*\}/g, "").replace(/\{[^}]*\}/g, "") || xpath;
}

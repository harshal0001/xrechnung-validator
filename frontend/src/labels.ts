/**
 * Every human-facing string, in both languages the service explains in.
 *
 * The service speaks in codes because codes are stable. A person reading a
 * report should not have to. The language a person picks applies to the
 * explanations the API returns AND to the page around them — an English
 * explanation under German chrome would read as a bug, not a feature.
 */

import type { Severity } from "./api";

export type Lang = "de" | "en";
export const LANGS: readonly Lang[] = ["de", "en"];

export const SEVERITY_LABEL: Record<Lang, Record<Severity, string>> = {
  de: { fatal: "Struktureller Fehler", error: "Fehler", warning: "Hinweis", info: "Information" },
  en: { fatal: "Structural error", error: "Error", warning: "Warning", info: "Information" },
};

/** What each severity means for whether the invoice can be sent. */
export const SEVERITY_MEANING: Record<Lang, Record<Severity, string>> = {
  de: {
    fatal: "Die Datei entspricht nicht dem Schema. Die Rechnung kann nicht gelesen werden.",
    error: "Eine Geschäftsregel ist verletzt. Die Rechnung würde abgewiesen.",
    warning: "Nicht blockierend, aber prüfenswert.",
    info: "Nur zur Kenntnis. Kein Handlungsbedarf.",
  },
  en: {
    fatal: "The file does not match the schema. The invoice cannot be read.",
    error: "A business rule is violated. The invoice would be rejected.",
    warning: "Not blocking, but worth checking.",
    info: "For information only. Nothing to do.",
  },
};

export const SYNTAX_LABEL: Record<string, string> = { UBL: "UBL 2.1", CII: "UN/CEFACT CII" };

export const SOURCE_LABEL: Record<Lang, Record<string, string>> = {
  de: { xml: "XML-Datei", "zugferd-pdf": "ZUGFeRD-PDF" },
  en: { xml: "XML file", "zugferd-pdf": "ZUGFeRD PDF" },
};

/** Page chrome. Keyed by language so a toggle swaps the whole page at once. */
export const UI: Record<Lang, Record<string, string>> = {
  de: {
    title: "XRechnung prüfen",
    lede: "Rechnung als XML (UBL oder UN/CEFACT CII) oder als ZUGFeRD-PDF hochladen. Geprüft wird gegen EN 16931 und den KoSIT-Regelsatz für XRechnung.",
    dropMain: "Datei hierher ziehen oder klicken",
    dropHint: "XML oder PDF · maximal 16 MB",
    dropAria: "Rechnung auswählen oder hierher ziehen",
    explainOption: "Erklärungen anzeigen",
    explainNote: "Nur geprüfte Erklärungen werden ausgeliefert.",
    checking: "wird geprüft …",
    failedTitle: "konnte nicht geprüft werden",
    clean: "Keine blockierenden Fehler",
    cleanSub: "Die Rechnung erfüllt die geprüften Regeln.",
    blocking: "blockierende(r) Fehler",
    blockingSub: "Die Rechnung würde in dieser Form abgewiesen.",
    thinProfile: "Dieses ZUGFeRD-Profil enthält zu wenige Felder für die deutsche Rechnungspflicht. Geschäftsregeln wurden nicht geprüft.",
    file: "Datei", format: "Format", source: "Quelle", profile: "Profil", ruleset: "Regelsatz", duration: "Dauer",
    where: "Fundstelle", value: "Wert", context: "Hintergrund",
    provenance: "Geprüft gegen KoSIT-Regelsatz",
    unreachable: "Der Dienst ist nicht erreichbar.",
    failedGeneric: "Die Prüfung ist fehlgeschlagen",
    unexpected: "Unerwarteter Fehler bei der Prüfung.",
    language: "Sprache",
    orSample: "Oder ein Beispiel ausprobieren",
    showSource: "Im Dokument zeigen",
    hideSource: "Ausblenden",
    contextNote: "Das Element fehlt. Erwartet wird es innerhalb dieses Elements.",
    noSource: "Die Stelle konnte im Dokument nicht aufgelöst werden.",
    copySummary: "Zusammenfassung kopieren",
    copied: "Kopiert",
    blockingOnly: "Nur blockierende",
    ruleSource: "Regeltext nachschlagen",
    findingsHidden: "ausgeblendet",
  },
  en: {
    title: "Check an XRechnung",
    lede: "Upload an invoice as XML (UBL or UN/CEFACT CII) or as a ZUGFeRD PDF. It is checked against EN 16931 and the KoSIT rule set for XRechnung.",
    dropMain: "Drop a file here or click",
    dropHint: "XML or PDF · 16 MB maximum",
    dropAria: "Choose an invoice or drop it here",
    explainOption: "Show explanations",
    explainNote: "Only reviewed explanations are served.",
    checking: "is being checked …",
    failedTitle: "could not be checked",
    clean: "No blocking errors",
    cleanSub: "The invoice satisfies the rules checked.",
    blocking: "blocking error(s)",
    blockingSub: "The invoice would be rejected as it stands.",
    thinProfile: "This ZUGFeRD profile carries too few fields for the German e-invoicing mandate. Business rules were not evaluated.",
    file: "File", format: "Format", source: "Source", profile: "Profile", ruleset: "Rule set", duration: "Duration",
    where: "Location", value: "Value", context: "Background",
    provenance: "Checked against KoSIT rule set",
    unreachable: "The service cannot be reached.",
    failedGeneric: "The check failed",
    unexpected: "Unexpected error during the check.",
    language: "Language",
    orSample: "Or try a sample",
    showSource: "Show in document",
    hideSource: "Hide",
    contextNote: "The element is missing. It is expected inside this element.",
    noSource: "The location could not be resolved in the document.",
    copySummary: "Copy summary",
    copied: "Copied",
    blockingOnly: "Blocking only",
    ruleSource: "Look up the rule text",
    findingsHidden: "hidden",
  },
};

/**
 * The EQName paths the validators emit are precise and unreadable. Strip the
 * namespace braces for display, keeping the element path a person can follow.
 * The full value stays available — this is presentation, not truth.
 */
export function readablePath(xpath: string): string {
  return xpath.replace(/Q\{[^}]*\}/g, "").replace(/\{[^}]*\}/g, "") || xpath;
}


/** The bundled demonstration invoices, described in both languages. */
export interface Sample {
  file: string;
  label: Record<Lang, string>;
  note: Record<Lang, string>;
}

export const SAMPLES: readonly Sample[] = [
  {
    file: "clean.xml",
    label: { de: "Gültige Rechnung", en: "Valid invoice" },
    note: { de: "Offizielle Referenznachricht", en: "Official reference message" },
  },
  {
    file: "missing-buyer-reference.xml",
    label: { de: "Käuferreferenz fehlt", en: "Buyer reference missing" },
    note: { de: "verletzt BR-DE-15", en: "breaks BR-DE-15" },
  },
  {
    file: "totals-mismatch.xml",
    label: { de: "Summe stimmt nicht", en: "Totals do not add up" },
    note: { de: "verletzt BR-CO-10 und BR-CO-13", en: "breaks BR-CO-10 and BR-CO-13" },
  },
];

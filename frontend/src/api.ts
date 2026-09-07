/**
 * The shapes the service returns, and the one call the UI makes.
 *
 * These mirror `xrv.core.models`. They are hand-written rather than generated
 * because there are two of them and a code generation step for that would cost
 * more than it saves — but they are the contract, so they carry the same names
 * the API uses rather than convenient renames.
 */

export type Severity = "fatal" | "error" | "warning" | "info";

export interface Finding {
  rule_id: string;
  severity: Severity;
  /** Verbatim from the KoSIT artifact. Always present. */
  rule_text: string;
  /** Where in the document, as the validator reported it. */
  xpath: string;
  offending_value: string | null;
  /** Plain German, when a reviewed explanation exists for this rule. */
  explanation: string | null;
}

export interface ValidationReport {
  syntax: "UBL" | "CII";
  source: "xml" | "zugferd-pdf";
  profile: string | null;
  mandate_ready: boolean;
  ruleset_version: string;
  ruleset_sha256: string;
  findings: Finding[];
  duration_ms: number;
}

/** Findings at these severities mean the invoice would be rejected. */
export const BLOCKING: ReadonlySet<Severity> = new Set<Severity>(["fatal", "error"]);

export function isBlocking(finding: Finding): boolean {
  return BLOCKING.has(finding.severity);
}

export function isValid(report: ValidationReport): boolean {
  return !report.findings.some(isBlocking);
}

/** An error the service explained, as opposed to one that just happened. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly kind: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function validate(file: File, explain: boolean): Promise<ValidationReport> {
  const body = new FormData();
  body.append("file", file);

  let response: Response;
  try {
    response = await fetch(`/validate?explain=${explain}`, { method: "POST", body });
  } catch {
    throw new ApiError(0, "unreachable", "Der Dienst ist nicht erreichbar.");
  }

  if (!response.ok) {
    // The service explains its refusals; show what it said rather than a status
    // code, which tells the person nothing about what to do next.
    const detail = await response
      .json()
      .then((body: { error?: string; detail?: string }) => body)
      .catch(() => ({}) as { error?: string; detail?: string });
    throw new ApiError(
      response.status,
      detail.error ?? "error",
      detail.detail ?? `Die Prüfung ist fehlgeschlagen (HTTP ${response.status}).`,
    );
  }

  return (await response.json()) as ValidationReport;
}

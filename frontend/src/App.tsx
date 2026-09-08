import { useCallback, useRef, useState } from "react";
import { ApiError, isBlocking, isValid, validate } from "./api";
import type { Finding, Severity, ValidationReport } from "./api";
import { SEVERITY_LABEL, SEVERITY_MEANING, SOURCE_LABEL, SYNTAX_LABEL, readablePath } from "./labels";

type State =
  | { status: "idle" }
  | { status: "checking"; filename: string }
  | { status: "done"; filename: string; report: ValidationReport }
  | { status: "failed"; filename: string; error: ApiError };

const SEVERITY_ORDER: Severity[] = ["fatal", "error", "warning", "info"];

function bySeverity(a: Finding, b: Finding): number {
  return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
}

export default function App() {
  const [state, setState] = useState<State>({ status: "idle" });
  const [explain, setExplain] = useState(true);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const check = useCallback(
    async (file: File) => {
      setState({ status: "checking", filename: file.name });
      try {
        const report = await validate(file, explain);
        setState({ status: "done", filename: file.name, report });
      } catch (caught) {
        const error =
          caught instanceof ApiError
            ? caught
            : new ApiError(0, "unknown", "Unerwarteter Fehler bei der Prüfung.");
        setState({ status: "failed", filename: file.name, error });
      }
    },
    [explain],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragging(false);
      const file = event.dataTransfer.files[0];
      if (file) void check(file);
    },
    [check],
  );

  return (
    <div className="page">
      <header>
        <h1>XRechnung prüfen</h1>
        <p className="lede">
          Rechnung als XML (UBL oder UN/CEFACT CII) oder als ZUGFeRD-PDF hochladen. Geprüft wird
          gegen EN 16931 und den KoSIT-Regelsatz für XRechnung.
        </p>
      </header>

      <section
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") input.current?.click();
        }}
        role="button"
        tabIndex={0}
        aria-label="Rechnung auswählen oder hierher ziehen"
      >
        <input
          ref={input}
          type="file"
          accept=".xml,.pdf,application/xml,text/xml,application/pdf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void check(file);
          }}
        />
        <p className="dropzone__main">Datei hierher ziehen oder klicken</p>
        <p className="dropzone__hint">XML oder PDF · maximal 16 MB</p>
      </section>

      <label className="option">
        <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
        <span>
          Erklärungen anzeigen
          <em>Nur geprüfte Erklärungen werden ausgeliefert.</em>
        </span>
      </label>

      {state.status === "checking" && (
        <p className="status" role="status">
          {state.filename} wird geprüft …
        </p>
      )}

      {state.status === "failed" && <Failure error={state.error} filename={state.filename} />}

      {state.status === "done" && <Report filename={state.filename} report={state.report} />}
    </div>
  );
}

function Failure({ error, filename }: { error: ApiError; filename: string }) {
  return (
    <section className="panel panel--problem" role="alert">
      <h2>{filename} konnte nicht geprüft werden</h2>
      <p>{error.message}</p>
    </section>
  );
}

function Report({ filename, report }: { filename: string; report: ValidationReport }) {
  const blocking = report.findings.filter(isBlocking);
  const other = report.findings.filter((f) => !isBlocking(f));
  const clean = isValid(report);

  return (
    <section className="panel">
      <header className={`verdict verdict--${clean ? "ok" : "bad"}`}>
        <h2>{clean ? "Keine blockierenden Fehler" : `${blocking.length} blockierende(r) Fehler`}</h2>
        <p>
          {clean
            ? "Die Rechnung erfüllt die geprüften Regeln."
            : "Die Rechnung würde in dieser Form abgewiesen."}
        </p>
      </header>

      {!report.mandate_ready && (
        <p className="notice">
          Dieses ZUGFeRD-Profil enthält zu wenige Felder für die deutsche Rechnungspflicht.
          Geschäftsregeln wurden nicht geprüft.
        </p>
      )}

      <dl className="facts">
        <Fact label="Datei" value={filename} />
        <Fact label="Format" value={SYNTAX_LABEL[report.syntax] ?? report.syntax} />
        <Fact label="Quelle" value={SOURCE_LABEL[report.source] ?? report.source} />
        {report.profile && <Fact label="Profil" value={report.profile} />}
        <Fact label="Regelsatz" value={report.ruleset_version} />
        <Fact label="Dauer" value={`${Math.round(report.duration_ms)} ms`} />
      </dl>

      {report.findings.length > 0 && (
        <ul className="findings">
          {[...blocking, ...other].sort(bySeverity).map((finding, index) => (
            <FindingRow key={`${finding.rule_id}-${index}`} finding={finding} />
          ))}
        </ul>
      )}

      <p className="provenance">
        Geprüft gegen KoSIT-Regelsatz {report.ruleset_version} · SHA-256{" "}
        <code>{report.ruleset_sha256.slice(0, 16)}…</code>
      </p>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li className={`finding finding--${finding.severity}`}>
      <div className="finding__head">
        <code className="finding__rule">{finding.rule_id}</code>
        <span className="finding__severity" title={SEVERITY_MEANING[finding.severity]}>
          {SEVERITY_LABEL[finding.severity]}
        </span>
      </div>

      {/* The explanation leads when there is one; the normative text is always
          shown underneath, because that is what the finding is grounded in and
          the reader should be able to check it. Context is marked as editorial —
          it is useful and human-approved, but it is not in any rule text, and
          presenting it as though it were would be a quiet lie. */}
      {finding.explanation && <p className="finding__explanation">{finding.explanation}</p>}
      {finding.context && (
        <p className="finding__context">
          <span className="finding__context-label">Hintergrund</span>
          {finding.context}
        </p>
      )}
      <p className="finding__rule-text">{finding.rule_text}</p>

      <p className="finding__where">
        <span>Fundstelle</span>
        <code title={finding.xpath}>{readablePath(finding.xpath)}</code>
      </p>

      {finding.offending_value && (
        <p className="finding__value">
          <span>Wert</span>
          <code>{finding.offending_value}</code>
        </p>
      )}
    </li>
  );
}

import { useCallback, useRef, useState } from "react";
import { ApiError, isBlocking, isValid, validate } from "./api";
import type { Finding, Severity, ValidationReport } from "./api";
import { LANGS, SEVERITY_LABEL, SEVERITY_MEANING, SOURCE_LABEL, SYNTAX_LABEL, UI, readablePath } from "./labels";
import type { Lang } from "./labels";

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
  const [lang, setLang] = useState<Lang>("de");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const t = UI[lang];

  const check = useCallback(
    async (file: File) => {
      setState({ status: "checking", filename: file.name });
      try {
        const report = await validate(file, explain, lang, t.unreachable ?? "");
        setState({ status: "done", filename: file.name, report });
      } catch (caught) {
        const error =
          caught instanceof ApiError
            ? caught
            : new ApiError(0, "unknown", t.unexpected ?? "");
        setState({ status: "failed", filename: file.name, error });
      }
    },
    [explain, lang, t],
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
        <div className="masthead">
          <h1>{t.title}</h1>
          <div className="lang" role="group" aria-label={t.language}>
            {LANGS.map((code) => (
              <button
                key={code}
                type="button"
                className={code === lang ? "lang__btn lang__btn--on" : "lang__btn"}
                aria-pressed={code === lang}
                onClick={() => setLang(code)}
              >
                {code.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <p className="lede">{t.lede}</p>
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
        aria-label={t.dropAria}
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
        <p className="dropzone__main">{t.dropMain}</p>
        <p className="dropzone__hint">{t.dropHint}</p>
      </section>

      <label className="option">
        <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
        <span>
          {t.explainOption}
          <em>{t.explainNote}</em>
        </span>
      </label>

      {state.status === "checking" && (
        <p className="status" role="status">
          {state.filename} {t.checking}
        </p>
      )}

      {state.status === "failed" && (
        <Failure error={state.error} filename={state.filename} lang={lang} />
      )}

      {state.status === "done" && (
        <Report filename={state.filename} report={state.report} lang={lang} />
      )}
    </div>
  );
}

function Failure({ error, filename, lang }: { error: ApiError; filename: string; lang: Lang }) {
  const t = UI[lang];
  return (
    <section className="panel panel--problem" role="alert">
      <h2>
        {filename} {t.failedTitle}
      </h2>
      <p>{error.message}</p>
    </section>
  );
}

function Report({ filename, report, lang }: { filename: string; report: ValidationReport; lang: Lang }) {
  const t = UI[lang];
  const blocking = report.findings.filter(isBlocking);
  const other = report.findings.filter((f) => !isBlocking(f));
  const clean = isValid(report);

  return (
    <section className="panel">
      <header className={`verdict verdict--${clean ? "ok" : "bad"}`}>
        <h2>{clean ? t.clean : `${blocking.length} ${t.blocking}`}</h2>
        <p>{clean ? t.cleanSub : t.blockingSub}</p>
      </header>

      {!report.mandate_ready && <p className="notice">{t.thinProfile}</p>}

      <dl className="facts">
        <Fact label={t.file ?? ""} value={filename} />
        <Fact label={t.format ?? ""} value={SYNTAX_LABEL[report.syntax] ?? report.syntax} />
        <Fact label={t.source ?? ""} value={SOURCE_LABEL[lang][report.source] ?? report.source} />
        {report.profile && <Fact label={t.profile ?? ""} value={report.profile} />}
        <Fact label={t.ruleset ?? ""} value={report.ruleset_version} />
        <Fact label={t.duration ?? ""} value={`${Math.round(report.duration_ms)} ms`} />
      </dl>

      {report.findings.length > 0 && (
        <ul className="findings">
          {[...blocking, ...other].sort(bySeverity).map((finding, index) => (
            <FindingRow key={`${finding.rule_id}-${index}`} finding={finding} lang={lang} />
          ))}
        </ul>
      )}

      <p className="provenance">
        {t.provenance} {report.ruleset_version} · SHA-256{" "}
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

function FindingRow({ finding, lang }: { finding: Finding; lang: Lang }) {
  const t = UI[lang];
  return (
    <li className={`finding finding--${finding.severity}`}>
      <div className="finding__head">
        <code className="finding__rule">{finding.rule_id}</code>
        <span className="finding__severity" title={SEVERITY_MEANING[lang][finding.severity]}>
          {SEVERITY_LABEL[lang][finding.severity]}
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
          <span className="finding__context-label">{t.context}</span>
          {finding.context}
        </p>
      )}
      <p className="finding__rule-text">{finding.rule_text}</p>

      <p className="finding__where">
        <span>{t.where}</span>
        <code title={finding.xpath}>{readablePath(finding.xpath)}</code>
      </p>

      {finding.offending_value && (
        <p className="finding__value">
          <span>{t.value}</span>
          <code>{finding.offending_value}</code>
        </p>
      )}
    </li>
  );
}

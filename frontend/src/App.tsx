import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, isBlocking, isValid, ruleSourceUrl, validate } from "./api";
import type { Finding, Severity, ValidationReport } from "./api";
import {
  LANGS,
  SAMPLES,
  SEVERITY_LABEL,
  SEVERITY_MEANING,
  SOURCE_LABEL,
  SYNTAX_LABEL,
  UI,
  readablePath,
} from "./labels";
import type { Lang } from "./labels";
import { locate } from "./locate";
import { THEMES, applyTheme, storedTheme } from "./theme";
import type { Theme } from "./theme";

type State =
  | { status: "idle" }
  | { status: "checking"; filename: string }
  | { status: "done"; filename: string; report: ValidationReport }
  | { status: "failed"; filename: string; error: ApiError };

const THEME_LABEL: Record<Theme, string> = {
  system: "themeSystem",
  light: "themeLight",
  dark: "themeDark",
};

const SEVERITY_ORDER: Severity[] = ["fatal", "error", "warning", "info"];

function bySeverity(a: Finding, b: Finding): number {
  return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
}

export default function App() {
  const [state, setState] = useState<State>({ status: "idle" });
  const [lang, setLang] = useState<Lang>("de");
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // The last file checked, so changing a toggle re-runs against it. Without
  // this, ticking "show explanations" after a result does nothing until the
  // next upload — the value was read when the request was made — and switching
  // language leaves the explanations in the language you switched away from.
  const lastFile = useRef<File | null>(null);
  const t = UI[lang];

  // The tab title and the document language are part of the page too. Leaving
  // them in German while everything else switched would show in the browser tab
  // and, more importantly, tell a screen reader to pronounce English as German.
  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = t.title ?? document.title;
  }, [lang, t]);

  const check = useCallback(
    async (file: File) => {
      lastFile.current = file;
      setState({ status: "checking", filename: file.name });
      try {
        const report = await validate(file, true, lang, t.unreachable ?? "");
        setState({ status: "done", filename: file.name, report });
      } catch (caught) {
        const error =
          caught instanceof ApiError ? caught : new ApiError(0, "unknown", t.unexpected ?? "");
        setState({ status: "failed", filename: file.name, error });
      }
    },
    [lang, t],
  );

  // Re-check when the language changes, so the explanations come back in the
  // language now selected rather than the one switched away from. `check` is
  // remade exactly when the language changes, so depending on it is depending
  // on the language — and says so to the linter without an exception.
  useEffect(() => {
    if (lastFile.current) void check(lastFile.current);
  }, [check]);

  /** Fetch a bundled sample and run it through the same path as an upload. */
  const checkSample = useCallback(
    async (fileName: string) => {
      try {
        const response = await fetch(`/samples/${fileName}`);
        const text = await response.text();
        await check(new File([text], fileName, { type: "application/xml" }));
      } catch {
        setState({
          status: "failed",
          filename: fileName,
          error: new ApiError(0, "unreachable", t.unreachable ?? ""),
        });
      }
    },
    [check, t],
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
          <div className="controls">
            <div className="switch" role="group" aria-label={t.theme}>
              {THEMES.map((option) => {
                const label = t[THEME_LABEL[option]] ?? option;
                return (
                  <button
                    key={option}
                    type="button"
                    className="switch__btn"
                    aria-pressed={option === theme}
                    aria-label={label}
                    title={label}
                    onClick={() => {
                      setTheme(option);
                      applyTheme(option);
                    }}
                  >
                    <ThemeIcon theme={option} />
                  </button>
                );
              })}
            </div>
            <div className="switch" role="group" aria-label={t.language}>
              {LANGS.map((code) => (
                <button
                  key={code}
                  type="button"
                  className="switch__btn switch__btn--text"
                  aria-pressed={code === lang}
                  onClick={() => setLang(code)}
                >
                  {code.toUpperCase()}
                </button>
              ))}
            </div>
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
        <svg className="dropzone__icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 15V4m0 0-4 4m4-4 4 4M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" />
        </svg>
        <p className="dropzone__main">{t.dropMain}</p>
        <p className="dropzone__hint">{t.dropHint}</p>
      </section>

      {/* Most visitors will not have an XRechnung file to hand. Without this the
          page is a dropzone they cannot use, and they leave without seeing it work. */}
      <section className="samples">
        <p className="samples__label">{t.orSample}</p>
        <div className="samples__row">
          {SAMPLES.map((sample) => (
            <button
              key={sample.file}
              type="button"
              className="sample"
              onClick={() => void checkSample(sample.file)}
            >
              <span className="sample__label">{sample.label[lang]}</span>
              <span className="sample__note">{sample.note[lang]}</span>
            </button>
          ))}
        </div>
      </section>

      {state.status === "checking" && (
        <p className="status" role="status">
          {state.filename} {t.checking}
        </p>
      )}

      {state.status === "failed" && (
        <Failure error={state.error} filename={state.filename} lang={lang} />
      )}

      {state.status === "done" && (
        <Report
          filename={state.filename}
          report={state.report}
          lang={lang}
        />
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

/** A plain-text rendering, for pasting into an email to whoever sent the invoice. */
function summarise(report: ValidationReport, filename: string, lang: Lang): string {
  const t = UI[lang];
  const lines = [
    `${filename} — ${report.scenario ?? report.syntax}, ${report.ruleset_version}`,
    "",
    ...report.findings.flatMap((finding) => [
      `[${finding.rule_id}] ${SEVERITY_LABEL[lang][finding.severity]}`,
      `  ${finding.explanation ?? finding.rule_text}`,
      ...(finding.context ? [`  ${finding.context}`] : []),
      `  ${t.where}: ${readablePath(finding.xpath)}`,
      "",
    ]),
    `${t.provenance} ${report.ruleset_version} · ${report.ruleset_sha256.slice(0, 16)}…`,
  ];
  return lines.join("\n");
}

function Report({
  filename,
  report,
  lang,
}: {
  filename: string;
  report: ValidationReport;
  lang: Lang;
}) {
  const t = UI[lang];
  const [copied, setCopied] = useState(false);
  const blocking = report.findings.filter(isBlocking);
  const clean = isValid(report);

  const shown = useMemo(() => [...report.findings].sort(bySeverity), [report.findings]);

  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(summarise(report, filename, lang)).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    });
  }, [report, filename, lang]);

  return (
    <section className="panel">
      <header className={`verdict verdict--${clean ? "ok" : "bad"}`}>
        <div>
          <h2>
            {clean
              ? t.clean
              : `${blocking.length} ${blocking.length === 1 ? t.blockingOne : t.blockingMany}`}
          </h2>
          <p>{clean ? t.cleanSub : t.blockingSub}</p>
        </div>
        {report.findings.length > 0 && (
          <button type="button" className="copy" onClick={copy}>
            {copied ? t.copied : t.copySummary}
          </button>
        )}
      </header>

      {!report.mandate_ready && <p className="notice">{t.thinProfile}</p>}

      <dl className="facts">
        <Fact label={t.file ?? ""} value={filename} />
        <Fact label={t.format ?? ""} value={SYNTAX_LABEL[report.syntax] ?? report.syntax} />
        <Fact label={t.source ?? ""} value={SOURCE_LABEL[lang][report.source] ?? report.source} />
        {report.profile && <Fact label={t.profile ?? ""} value={report.profile} />}
        <Fact label={t.ruleset ?? ""} value={report.ruleset_version} />
        {report.scenario && <Fact label={t.checkedAs ?? ""} value={report.scenario} />}
        <Fact label={t.duration ?? ""} value={`${Math.round(report.duration_ms)} ms`} />
      </dl>

      {shown.length > 0 && (
        <ul className="findings">
          {shown.map((finding, index) => (
            <FindingRow
              key={`${finding.rule_id}-${index}`}
              finding={finding}
              lang={lang}
              sourceXml={report.source_xml}
            />
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

/** Sun, moon, and a screen for "follow the system". Stroke icons, drawn in the
 *  text colour, so they change with the theme like everything else. */
function ThemeIcon({ theme }: { theme: Theme }) {
  return (
    <svg className="switch__icon" viewBox="0 0 24 24" aria-hidden="true">
      {theme === "light" && (
        <>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4" />
        </>
      )}
      {theme === "dark" && <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />}
      {theme === "system" && (
        <>
          <rect x="3" y="4" width="18" height="12" rx="2" />
          <path d="M8 20h8m-4-4v4" />
        </>
      )}
    </svg>
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

function FindingRow({
  finding,
  lang,
  sourceXml,
}: {
  finding: Finding;
  lang: Lang;
  sourceXml: string | null;
}) {
  const t = UI[lang];
  const [open, setOpen] = useState(false);

  // Resolved lazily: a report with fifteen findings would otherwise parse the
  // document fifteen times before anything is on screen.
  const excerpt = useMemo(
    () => (open && sourceXml ? locate(sourceXml, finding.xpath) : null),
    [open, sourceXml, finding.xpath],
  );
  const sourceUrl = ruleSourceUrl(finding.rule_id);

  return (
    <li className={`finding finding--${finding.severity}`}>
      <div className="finding__head">
        <code className="finding__rule">{finding.rule_id}</code>
        <span className="finding__severity" title={SEVERITY_MEANING[lang][finding.severity]}>
          {SEVERITY_LABEL[lang][finding.severity]}
        </span>
        {sourceUrl && (
          <a className="finding__source-link" href={sourceUrl} target="_blank" rel="noreferrer">
            {t.ruleSource}
          </a>
        )}
      </div>

      {/* The explanation leads when there is one; the normative text is always
          shown underneath, because that is what the finding is grounded in and
          the reader should be able to check it. Context is marked as editorial —
          it is useful and human-approved, but it is not in any rule text, and
          presenting it as though it were would be a quiet lie. */}
      {finding.explanation ? (
        <p className="finding__explanation">{finding.explanation}</p>
      ) : (
        // Only 25 of the rule set's 1646 rules have a reviewed explanation. Say
        // so where a reader is owed a reason — on a finding that would get the
        // invoice rejected — and stay quiet on an informational one, where the
        // note would be noise rather than an answer.
        isBlocking(finding) && <p className="finding__unexplained">{t.noExplanation}</p>
      )}
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
        {sourceXml && (
          <button type="button" className="finding__toggle" onClick={() => setOpen(!open)}>
            {open ? t.hideSource : t.showSource}
          </button>
        )}
      </p>

      {finding.offending_value && (
        <p className="finding__value">
          <span>{t.value}</span>
          <code>{finding.offending_value}</code>
        </p>
      )}

      {open &&
        (excerpt ? (
          <div className="excerpt">
            {excerpt.isContext ? (
              <p className="excerpt__note">{t.contextNote}</p>
            ) : (
              <p className="excerpt__caption">{t.where}</p>
            )}
            <pre className="excerpt__code">
              {excerpt.lines.map((line) => (
                <span
                  key={line.number}
                  className={line.marked ? "excerpt__line excerpt__line--marked" : "excerpt__line"}
                >
                  <span className="excerpt__num">{line.number}</span>
                  {line.text}
                  {"\n"}
                </span>
              ))}
            </pre>
          </div>
        ) : (
          <p className="excerpt__note">{t.noSource}</p>
        ))}
    </li>
  );
}

"""Run the KoSIT stylesheets with Saxon.

SaxonC-HE is the reason this project ships as a container: it bundles a native
library, and it is the only practical way to execute XSLT 2.0 from Python. KoSIT
publishes Schematron compiled to XSLT 2.0, and lxml only does 1.0.

Stylesheets are compiled once, up front. That looks wasteful and is not: on
Lambda the INIT phase is unbilled and runs at full vCPU, and compiling all four
measured at 1.7s there — free, and it moves the cost off the request path
entirely. Compiling lazily would put ~400ms on the first request of every cold
container instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from saxonche import PySaxonProcessor, PyXsltExecutable

from xrv.core import Finding, Syntax
from xrv.ingest import parse, to_text
from xrv.rules import Ruleset, Scenario
from xrv.validate.scenario import ScenarioMatcher
from xrv.validate.structure import StructureValidator
from xrv.validate.svrl import parse_svrl


class ValidationError(RuntimeError):
    """The document could not be run through the rules at all."""


@dataclass(frozen=True)
class Evaluation:
    """What validating one document found, and what it was validated as."""

    findings: tuple[Finding, ...]
    #: The KoSIT scenario that recognised the document. None when none did, and
    #: when the document failed structurally, because nothing past the schema ran.
    scenario: Scenario | None = None
    #: Stylesheets held for this syntax that the scenario does not validate
    #: with, and which therefore did not run. Empty findings from a rule set
    #: that was never applied are not a pass, so a caller has to be able to tell.
    skipped: tuple[Path, ...] = ()


class ValidationEngine:
    """Compiled rules for one ruleset version.

    Not thread-safe: a Saxon processor and its compiled stylesheets belong to the
    thread that made them. Give each worker its own engine, or serialise access.
    """

    def __init__(self, ruleset: Ruleset, syntaxes: Iterable[Syntax] | None = None) -> None:
        self.ruleset = ruleset
        wanted = tuple(syntaxes) if syntaxes is not None else tuple(Syntax)
        self._processor = PySaxonProcessor(license=False)
        self._compiled: dict[Syntax, tuple[tuple[Path, PyXsltExecutable], ...]] = {}
        self.structure = StructureValidator(ruleset, syntaxes=wanted)
        self._scenarios = ScenarioMatcher(self._processor, ruleset.scenarios)

        compiler = self._processor.new_xslt30_processor()
        for syntax in wanted:
            self._compiled[syntax] = tuple(
                (sheet, compiler.compile_stylesheet(stylesheet_file=str(sheet)))
                for sheet in ruleset.stylesheets(syntax)
            )

    @property
    def saxon_version(self) -> str:
        return str(self._processor.version)

    def findings(self, source: Path | str | bytes, syntax: Syntax) -> tuple[Finding, ...]:
        """Validate a document: structure first, then business rules.

        Structural failure stops the run. Business rules are written against a
        shape the schema guarantees, so evaluating them over a document the
        schema rejected reports on a structure that is not there — noise layered
        on top of the one finding that matters.

        Note that the two layers overlap: some EN 16931 rules restate a
        constraint the schema already enforces, so a document missing its invoice
        number fails structurally and never reaches the rule that says so. The
        FATAL finding is the honest answer in that case.
        """
        return self.evaluate(source, syntax).findings

    def evaluate(self, source: Path | str | bytes, syntax: Syntax) -> Evaluation:
        """`findings`, together with the scenario the document was recognised as."""
        payload = self._payload(source)
        structural = self.structure.findings(payload, syntax)
        if structural:
            return Evaluation(structural)
        return self._rules(payload, syntax)

    @staticmethod
    def _payload(source: Path | str | bytes) -> bytes:
        """Read the document once, whatever form it arrived in.

        A missing file is not a malformed one, and should not be reported as one.
        """
        if isinstance(source, bytes):
            return source
        path = Path(source)
        if not path.is_file():
            raise ValidationError(f"no such document: {path}")
        return path.read_bytes()

    def rule_findings(self, source: Path | str | bytes, syntax: Syntax) -> tuple[Finding, ...]:
        """Run the stylesheets that apply to this document and collect what fired.

        Which apply is the scenario's call. An XRechnung is validated against the
        EN 16931 core rules and then the German CIUS: it can satisfy the European
        rules and still breach the national restriction. A document that claims
        only EN 16931 is validated against the core alone — holding it to a CIUS
        it never claimed reports a valid invoice as broken. One no scenario
        recognises gets both, which is the stricter reading of a document that
        has not said what it is.

        Exposed separately from `findings` so the rule layer can be exercised on
        its own — a mutation that also breaks the schema would otherwise never
        reach the rule it was written to prove.
        """
        # Asked for before the document is read: a syntax this engine cannot
        # run is the caller's mistake whatever the document turns out to be.
        self._executables(syntax)
        return self._rules(self._payload(source), syntax).findings

    def reports(self, source: Path | str | bytes, syntax: Syntax) -> tuple[str, ...]:
        """The raw SVRL, one report per stylesheet that ran.

        For tooling that needs what a `Finding` leaves out — which rule contexts
        were evaluated at all, as opposed to which assertions failed.
        """
        self._executables(syntax)
        reports, _, _ = self._run(self._payload(source), syntax)
        return reports

    def _executables(self, syntax: Syntax) -> tuple[tuple[Path, PyXsltExecutable], ...]:
        try:
            return self._compiled[syntax]
        except KeyError:
            raise ValidationError(
                f"engine was not built for {syntax}; it has {sorted(self._compiled)}"
            ) from None

    def _run(
        self, payload: bytes, syntax: Syntax
    ) -> tuple[tuple[str, ...], Scenario | None, tuple[Path, ...]]:
        """Transform the document: the reports, its scenario, and what did not run."""
        held = self._executables(syntax)

        # Parsed here rather than handed to Saxon as a file path: the same bytes
        # then go through both layers, and a document declaring an encoding other
        # than UTF-8 survives, because the declaration was honoured on the way in.
        node = self._processor.parse_xml(xml_text=to_text(parse(payload)))

        scenario = self._scenarios.match(node)
        applies = held
        if scenario is not None:
            named = {self.ruleset.root / location for location in scenario.stylesheets}
            unknown = named - {sheet for sheet, _ in held}
            if unknown:
                # Running what we have and staying quiet about the rest would
                # report a document as checked against rules it never met.
                raise ValidationError(
                    f"scenario '{scenario.name}' validates with "
                    f"{sorted(str(sheet) for sheet in unknown)}, which this engine does not hold"
                )
            applies = tuple((sheet, executable) for sheet, executable in held if sheet in named)

        reports = []
        for _, executable in applies:
            try:
                svrl = executable.transform_to_string(xdm_node=node)
            except Exception as exc:  # saxonche raises bare exceptions
                raise ValidationError(f"transform failed: {exc}") from exc
            if svrl is None:
                raise ValidationError("transform returned nothing")
            reports.append(svrl)

        ran = {sheet for sheet, _ in applies}
        return tuple(reports), scenario, tuple(sheet for sheet, _ in held if sheet not in ran)

    def _rules(self, payload: bytes, syntax: Syntax) -> Evaluation:
        reports, scenario, skipped = self._run(payload, syntax)
        collected = tuple(finding for report in reports for finding in parse_svrl(report))
        if scenario is None:
            return Evaluation(collected)

        # A stylesheet grades each rule once, for every document it is run on.
        # The scenario grades it for this kind of document, and that is the
        # grade KoSIT's own validator reports.
        return Evaluation(
            tuple(f.with_severity(scenario.severity(f.rule_id, f.severity)) for f in collected),
            scenario,
            skipped,
        )

    def close(self) -> None:
        """Drop the compiled schemas and stylesheets, and shut the processor down.

        PySaxonProcessor exposes no explicit release in saxonche 13 — its
        __exit__ is the teardown — so closing means driving that.
        """
        self._compiled.clear()
        self.structure.close()
        self._processor.__exit__(None, None, None)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

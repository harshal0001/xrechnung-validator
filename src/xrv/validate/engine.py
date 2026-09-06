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
from pathlib import Path
from types import TracebackType
from typing import Self

from saxonche import PySaxonProcessor, PyXsltExecutable

from xrv.core import Finding, Syntax
from xrv.ingest import parse, to_text
from xrv.rules import Ruleset
from xrv.validate.structure import StructureValidator
from xrv.validate.svrl import parse_svrl


class ValidationError(RuntimeError):
    """The document could not be run through the rules at all."""


class ValidationEngine:
    """Compiled rules for one ruleset version.

    Not thread-safe: a Saxon processor and its compiled stylesheets belong to the
    thread that made them. Give each worker its own engine, or serialise access.
    """

    def __init__(self, ruleset: Ruleset, syntaxes: Iterable[Syntax] | None = None) -> None:
        self.ruleset = ruleset
        wanted = tuple(syntaxes) if syntaxes is not None else tuple(Syntax)
        self._processor = PySaxonProcessor(license=False)
        self._compiled: dict[Syntax, tuple[PyXsltExecutable, ...]] = {}
        self.structure = StructureValidator(ruleset, syntaxes=wanted)

        compiler = self._processor.new_xslt30_processor()
        for syntax in wanted:
            self._compiled[syntax] = tuple(
                compiler.compile_stylesheet(stylesheet_file=str(sheet))
                for sheet in ruleset.stylesheets(syntax)
            )

    @property
    def saxon_version(self) -> str:
        return str(self._processor.version)

    def findings(self, source: Path | str, syntax: Syntax) -> tuple[Finding, ...]:
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
        payload = self._payload(source)
        structural = self.structure.findings(payload, syntax)
        if structural:
            return structural
        return self.rule_findings(payload, syntax)

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
        """Run every stylesheet for `syntax` and collect what fired.

        Both the EN 16931 core rules and the German CIUS run, in that order. A
        document can satisfy the European rules and still breach the national
        restriction, so neither alone is an answer.

        Exposed separately from `findings` so the rule layer can be exercised on
        its own — a mutation that also breaks the schema would otherwise never
        reach the rule it was written to prove.
        """
        try:
            executables = self._compiled[syntax]
        except KeyError:
            raise ValidationError(
                f"engine was not built for {syntax}; it has {sorted(self._compiled)}"
            ) from None

        payload = self._payload(source)

        # Parsed here rather than handed to Saxon as a file path: the same bytes
        # then go through both layers, and a document declaring an encoding other
        # than UTF-8 survives, because the declaration was honoured on the way in.
        node = self._processor.parse_xml(xml_text=to_text(parse(payload)))

        collected: list[Finding] = []
        for executable in executables:
            try:
                svrl = executable.transform_to_string(xdm_node=node)
            except Exception as exc:  # saxonche raises bare exceptions
                raise ValidationError(f"transform failed: {exc}") from exc
            if svrl is None:
                raise ValidationError("transform returned nothing")
            collected.extend(parse_svrl(svrl))
        return tuple(collected)

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

"""Assembling a ValidationReport from an uploaded payload.

Holds the pieces that are expensive to build and cheap to reuse: the compiled
schemas and stylesheets, and the explanation catalogue. Everything here is
transport-agnostic, so the HTTP layer stays a thin translation of errors into
status codes.

One constraint shapes the design. SaxonC-HE is not thread-safe: a processor and
its compiled stylesheets belong to the thread that made them. Validation is
therefore serialised behind a lock. That caps throughput at one document at a
time per process, which is the honest trade for a service whose work is 27 ms of
CPU — the way to scale it is more worker processes, not more threads in one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from xrv.core import Finding, Severity, ValidationReport
from xrv.explain import Catalogue, CatalogueError, CatalogueProvider, NullProvider
from xrv.explain.port import ExplanationProvider
from xrv.ingest import Document, identify
from xrv.rules import Ruleset, RulesetNotFoundError, RulesetRegistry, default_registry
from xrv.validate import ValidationEngine

#: Reported instead of business rules when a ZUGFeRD profile is too thin to
#: validate. Not an error: the document is what it claims to be, it simply does
#: not carry what the German mandate requires.
PROFILE_TOO_THIN = "PROFILE-NOT-MANDATE-READY"


@dataclass
class ValidationService:
    """Validates payloads against one or more rule set versions."""

    registry: RulesetRegistry = field(default_factory=default_registry)
    catalogue_dir: Path | None = None
    _engines: dict[str, ValidationEngine] = field(default_factory=dict, init=False)
    _providers: dict[str, ExplanationProvider] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def warm(self, version: str | None = None) -> str:
        """Build everything the first request would otherwise pay for.

        Called at startup. On Lambda the init phase is unbilled and runs at full
        vCPU, so compiling here is free; on any platform it keeps ~2.4s off the
        first request.
        """
        ruleset = self.registry.get(version)
        with self._lock:
            self._engine_for(ruleset)
        return ruleset.version

    @property
    def ready_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._engines))

    def _engine_for(self, ruleset: Ruleset) -> ValidationEngine:
        engine = self._engines.get(ruleset.version)
        if engine is None:
            engine = ValidationEngine(ruleset)
            self._engines[ruleset.version] = engine
            self._providers[ruleset.version] = self._provider_for(ruleset)
        return engine

    def _provider_for(self, ruleset: Ruleset) -> ExplanationProvider:
        """The catalogue for this rule set version, or nothing.

        A missing catalogue is not an error. Explanations improve a report; the
        normative rule text is always there regardless.
        """
        if self.catalogue_dir is None:
            return NullProvider()
        try:
            return CatalogueProvider(Catalogue.for_ruleset(ruleset.version, self.catalogue_dir))
        except CatalogueError:
            return NullProvider()

    def validate(
        self, payload: bytes, *, explain: bool = False, version: str | None = None
    ) -> ValidationReport:
        """Identify, validate and describe one uploaded document."""
        started = time.perf_counter()
        document = identify(payload)
        ruleset = self.registry.get(version)

        with self._lock:
            engine = self._engine_for(ruleset)
            findings = self._findings(engine, document)
            if explain:
                findings = self._explain(findings, ruleset.version)

        return ValidationReport(
            syntax=document.syntax,
            source=document.source,
            profile=str(document.profile) if document.profile else None,
            mandate_ready=document.mandate_ready,
            ruleset_version=ruleset.version,
            ruleset_sha256=ruleset.sha256,
            findings=findings,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _findings(self, engine: ValidationEngine, document: Document) -> tuple[Finding, ...]:
        """Validate, unless the profile says there is nothing to validate against.

        A ZUGFeRD MINIMUM document has no line items. Running the full rule set
        over it produces dozens of failures for data the profile never claimed to
        carry, which reads as "your invoice is broken" when the truth is "this
        profile is not an invoice". Say that instead.
        """
        if not document.mandate_ready:
            return (
                Finding(
                    rule_id=PROFILE_TOO_THIN,
                    severity=Severity.WARNING,
                    rule_text=(
                        f"This document uses the {document.profile} profile, which carries "
                        f"too few fields to satisfy the German e-invoicing mandate. Business "
                        f"rules were not evaluated, because most would fail on data the "
                        f"profile does not claim to contain."
                    ),
                    xpath=f"/{document.root}",
                ),
            )
        return engine.findings(document.content, document.syntax)

    def _explain(self, findings: tuple[Finding, ...], version: str) -> tuple[Finding, ...]:
        """Attach explanation and editorial context, where a reviewed entry exists."""
        provider = self._providers.get(version, NullProvider())
        return tuple(
            finding.with_explanation(what, provider.context(finding))
            if (what := provider.explain(finding))
            else finding
            for finding in findings
        )

    def rulesets(self) -> list[dict[str, object]]:
        """Every rule set on disk, with the provenance behind its results."""
        available = []
        for ruleset in self.registry:
            available.append(
                {
                    "version": ruleset.version,
                    "sha256": ruleset.sha256,
                    "loaded": ruleset.version in self._engines,
                }
            )
        if not available:
            raise RulesetNotFoundError("no rule set has been fetched")
        return available

    def close(self) -> None:
        with self._lock:
            for engine in self._engines.values():
                engine.close()
            self._engines.clear()
            self._providers.clear()

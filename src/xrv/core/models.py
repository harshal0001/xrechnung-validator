"""The core data model.

Everything downstream hangs off `Finding`. In particular `explain/` is given a
`Finding` and returns a string — it never receives the invoice. Keeping that
signature honest is the whole safety story, so `Finding` carries the four things
an explanation may be grounded in (rule id, rule text, location, offending
value) and nothing that could leak document content beyond them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class Syntax(StrEnum):
    """The two XML syntaxes EN 16931 permits."""

    UBL = "UBL"
    CII = "CII"


class Source(StrEnum):
    """How the invoice arrived."""

    XML = "xml"
    ZUGFERD_PDF = "zugferd-pdf"


class Severity(StrEnum):
    """How badly a finding fails the document.

    FATAL is reserved for structural failure — the XSD rejected the document, so
    no business rule could be evaluated at all. The other three come from KoSIT
    and are mapped by `severity_from_kosit_flag`.
    """

    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def blocking(self) -> bool:
        """Whether this finding means the invoice would be rejected.

        WARNING and INFO are not blocking. This matters more than it looks: the
        KoSIT reference messages are valid by construction and still emit
        informational asserts, so treating every failed assertion as a failure
        invents false positives on the one corpus that exists to prove there are
        none.
        """
        return self in _BLOCKING


_BLOCKING = frozenset({Severity.FATAL, Severity.ERROR})

# KoSIT carries severity in the `flag` attribute of a Schematron assertion. The
# vocabulary in the 2026-08-31 configuration is exactly these three values —
# 833 fatal, 1191 warning, 2 information. `tests/unit/test_kosit_flags.py`
# re-derives that from the shipped stylesheets, so a new flag in a future
# release fails CI rather than being silently mapped here.
#
# Note that KoSIT's "fatal" is a *business rule* violation, not a structural
# one, so it maps to ERROR. FATAL stays reserved for XSD failures.
_KOSIT_FLAG_TO_SEVERITY = {
    "fatal": Severity.ERROR,
    "warning": Severity.WARNING,
    "information": Severity.INFO,
}

KNOWN_KOSIT_FLAGS = frozenset(_KOSIT_FLAG_TO_SEVERITY)


def severity_from_kosit_flag(flag: str | None) -> Severity:
    """Map a KoSIT `flag` attribute onto a `Severity`.

    An absent or unrecognised flag maps to ERROR. That direction is deliberate:
    a rule we do not understand should surface loudly rather than be quietly
    downgraded to a warning and disappear from the report.
    """
    if flag is None:
        return Severity.ERROR
    return _KOSIT_FLAG_TO_SEVERITY.get(flag.strip().lower(), Severity.ERROR)


class Finding(BaseModel):
    """One rule violation, and everything an explanation may be grounded in.

    Frozen on purpose. `explain/` fills `explanation` by producing a new copy via
    `with_explanation`, so an explainer cannot reach back and rewrite the rule
    text it was supposed to be grounded in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(min_length=1, description='Rule identifier, e.g. "BR-DE-15"')
    severity: Severity
    rule_text: str = Field(description="Verbatim assertion text from the KoSIT artifact")
    xpath: str = Field(description="Location of the offending node in the document")
    offending_value: str | None = None
    explanation: str | None = Field(
        default=None,
        description=(
            "Plain-German restatement of the rule, filled by explain/. Traceable "
            "to the official rule text."
        ),
    )
    context: str | None = Field(
        default=None,
        description=(
            "Editorial context — typical causes, consequences, background. Human "
            "approved but NOT derivable from the rule text, so it is kept separate "
            "rather than folded into `explanation`, which would present it as "
            "grounded when it is not."
        ),
    )

    @property
    def blocking(self) -> bool:
        return self.severity.blocking

    def with_explanation(self, explanation: str, context: str | None = None) -> Self:
        """Return a copy carrying an explanation. The original is unchanged.

        `context` stays a separate field on purpose: concatenating it into
        `explanation` would make editorial text indistinguishable from the
        grounded restatement, which is the one distinction this layer exists to
        keep.
        """
        return self.model_copy(update={"explanation": explanation, "context": context})


class ValidationReport(BaseModel):
    """The result of validating one document against one ruleset version.

    `ruleset_version` and `ruleset_sha256` are recorded on every report, not as
    decoration: XRechnung 4.0 is in flight, and a result that cannot say which
    rules produced it stops meaning anything the moment the rules move.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    syntax: Syntax
    source: Source
    profile: str | None = Field(default=None, description="ZUGFeRD profile, if applicable")
    mandate_ready: bool = Field(
        description="False for ZUGFeRD profiles below EN 16931, which cannot be validated fully"
    )
    ruleset_version: str
    ruleset_sha256: str
    findings: tuple[Finding, ...] = ()
    duration_ms: float = Field(ge=0)

    @property
    def valid(self) -> bool:
        """True when nothing blocking fired. Warnings and info do not count."""
        return not any(f.blocking for f in self.findings)

    def by_severity(self, severity: Severity) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is severity)

    @property
    def counts(self) -> dict[Severity, int]:
        """Findings per severity, including severities that did not fire."""
        return {s: sum(1 for f in self.findings if f.severity is s) for s in Severity}

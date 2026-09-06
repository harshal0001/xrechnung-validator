"""The domain contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xrv.core import (
    Finding,
    Severity,
    Source,
    Syntax,
    ValidationReport,
    severity_from_kosit_flag,
)


def finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "rule_id": "BR-DE-15",
        "severity": Severity.ERROR,
        "rule_text": "Der Käuferreferenz muss angegeben werden.",
        "xpath": "/Invoice/cbc:BuyerReference",
    }
    return Finding(**(base | overrides))  # type: ignore[arg-type]


class TestSeverityMapping:
    @pytest.mark.parametrize(
        ("flag", "expected"),
        [
            ("fatal", Severity.ERROR),
            ("warning", Severity.WARNING),
            ("information", Severity.INFO),
            ("FATAL", Severity.ERROR),
            ("  warning  ", Severity.WARNING),
        ],
    )
    def test_known_flags(self, flag: str, expected: Severity) -> None:
        assert severity_from_kosit_flag(flag) is expected

    def test_kosit_fatal_is_not_our_fatal(self) -> None:
        """KoSIT's "fatal" is a business rule violation; FATAL is reserved for XSD."""
        assert severity_from_kosit_flag("fatal") is Severity.ERROR

    @pytest.mark.parametrize("flag", [None, "", "bogus", "critical"])
    def test_unknown_flags_surface_as_errors(self, flag: str | None) -> None:
        """A rule we cannot classify must not be quietly downgraded out of the report."""
        assert severity_from_kosit_flag(flag) is Severity.ERROR

    @pytest.mark.parametrize(
        ("severity", "blocking"),
        [
            (Severity.FATAL, True),
            (Severity.ERROR, True),
            (Severity.WARNING, False),
            (Severity.INFO, False),
        ],
    )
    def test_blocking(self, severity: Severity, blocking: bool) -> None:
        assert severity.blocking is blocking


class TestFinding:
    def test_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            finding().rule_id = "BR-DE-16"  # type: ignore[misc]

    def test_with_explanation_does_not_mutate_the_original(self) -> None:
        """explain/ produces a copy, so it cannot rewrite the text it was grounded in."""
        original = finding()
        explained = original.with_explanation("Die Käuferreferenz fehlt.")

        assert original.explanation is None
        assert explained.explanation == "Die Käuferreferenz fehlt."
        assert explained.rule_text == original.rule_text

    def test_rejects_unknown_fields(self) -> None:
        """extra="forbid" is what stops invoice content being smuggled onto a Finding."""
        with pytest.raises(ValidationError):
            finding(invoice_xml="<Invoice/>")

    def test_requires_a_rule_id(self) -> None:
        with pytest.raises(ValidationError):
            finding(rule_id="")


def report(*findings: Finding, **overrides: object) -> ValidationReport:
    base: dict[str, object] = {
        "syntax": Syntax.UBL,
        "source": Source.XML,
        "mandate_ready": True,
        "ruleset_version": "2026-08-31",
        "ruleset_sha256": "b" * 64,
        "findings": findings,
        "duration_ms": 12.5,
    }
    return ValidationReport(**(base | overrides))  # type: ignore[arg-type]


class TestValidationReport:
    def test_empty_report_is_valid(self) -> None:
        assert report().valid is True

    def test_warnings_and_info_do_not_invalidate(self) -> None:
        """The reference corpus is valid by construction yet emits informational asserts."""
        r = report(
            finding(severity=Severity.WARNING),
            finding(severity=Severity.INFO, rule_id="BR-DE-TMP-32"),
        )
        assert r.valid is True

    @pytest.mark.parametrize("severity", [Severity.FATAL, Severity.ERROR])
    def test_blocking_findings_invalidate(self, severity: Severity) -> None:
        assert report(finding(severity=severity)).valid is False

    def test_counts_cover_every_severity(self) -> None:
        r = report(finding(severity=Severity.ERROR), finding(severity=Severity.ERROR))
        assert r.counts == {
            Severity.FATAL: 0,
            Severity.ERROR: 2,
            Severity.WARNING: 0,
            Severity.INFO: 0,
        }

    def test_by_severity(self) -> None:
        warning = finding(severity=Severity.WARNING)
        r = report(finding(severity=Severity.ERROR), warning)
        assert r.by_severity(Severity.WARNING) == (warning,)

    def test_provenance_is_required(self) -> None:
        """A report that cannot say which rules produced it is not a result."""
        with pytest.raises(ValidationError):
            ValidationReport(  # type: ignore[call-arg]
                syntax=Syntax.UBL,
                source=Source.XML,
                mandate_ready=True,
                duration_ms=1.0,
            )

    def test_duration_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            report(duration_ms=-1.0)

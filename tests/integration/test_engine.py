"""The engine against real KoSIT stylesheets and real reference messages.

The headline assertion here is that no reference message produces a blocking
finding. Those messages are valid by construction, so any blocking finding is a
false positive — and a validator with false positives is worse than none, because
it sends people looking for problems that are not there.

What this does *not* prove is rule coverage. Passing a corpus of valid documents
says nothing about whether a broken document would be caught; that needs mutated
fixtures, which do not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xrv.core import Finding, Severity, Syntax
from xrv.rules import Ruleset
from xrv.validate import ValidationEngine, ValidationError

SYNTAX_GLOB = {Syntax.UBL: "*_ubl.xml", Syntax.CII: "*_uncefact.xml"}


def invoices(corpus: Path, syntax: Syntax) -> list[Path]:
    return sorted(corpus.glob(SYNTAX_GLOB[syntax]))


class TestAgainstReferenceMessages:
    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_corpus_is_not_empty(self, corpus: Path, syntax: Syntax) -> None:
        """Guards the assertions below: an empty glob would pass all of them."""
        assert len(invoices(corpus, syntax)) >= 20

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_no_reference_message_produces_a_blocking_finding(
        self, engine: ValidationEngine, corpus: Path, syntax: Syntax
    ) -> None:
        false_positives: dict[str, list[str]] = {}
        for invoice in invoices(corpus, syntax):
            blocking = [f for f in engine.findings(invoice, syntax) if f.blocking]
            if blocking:
                false_positives[invoice.name] = sorted({f.rule_id for f in blocking})
        assert not false_positives, f"false positives on valid {syntax} messages: {false_positives}"

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_rules_actually_ran(
        self, engine: ValidationEngine, corpus: Path, syntax: Syntax
    ) -> None:
        """A parser that returned nothing would satisfy the test above.

        The reference messages do trip non-blocking rules, so some findings must
        come back — otherwise the zero above means the transform silently did
        nothing rather than that the documents are clean.
        """
        total = sum(len(engine.findings(i, syntax)) for i in invoices(corpus, syntax))
        assert total > 0


class TestFindingsAreWellFormed:
    @pytest.fixture(scope="class")
    @classmethod
    def sample(cls, engine: ValidationEngine, corpus: Path) -> tuple[Finding, ...]:
        found: list[Finding] = []
        for syntax in Syntax:
            for invoice in invoices(corpus, syntax):
                found.extend(engine.findings(invoice, syntax))
        assert found, "no findings at all — cannot check their shape"
        return tuple(found)

    def test_every_finding_names_a_rule(self, sample: tuple[Finding, ...]) -> None:
        assert not [f for f in sample if f.rule_id == "UNKNOWN"]

    def test_every_finding_has_rule_text(self, sample: tuple[Finding, ...]) -> None:
        """The explanation layer is grounded in this text; empty means ungrounded."""
        assert not [f for f in sample if not f.rule_text]

    def test_every_finding_has_a_location(self, sample: tuple[Finding, ...]) -> None:
        assert not [f for f in sample if not f.xpath]

    def test_rule_text_does_not_repeat_the_rule_id(self, sample: tuple[Finding, ...]) -> None:
        assert not [f for f in sample if f.rule_text.startswith(f"[{f.rule_id}]")]

    def test_nothing_is_explained_yet(self, sample: tuple[Finding, ...]) -> None:
        """explain/ has not run, so no finding may carry an explanation."""
        assert not [f for f in sample if f.explanation is not None]

    def test_severities_seen_are_the_non_blocking_ones(self, sample: tuple[Finding, ...]) -> None:
        assert {f.severity for f in sample} <= {Severity.WARNING, Severity.INFO}


class TestDeterminism:
    def test_the_same_invoice_gives_the_same_report(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        invoice = invoices(corpus, Syntax.UBL)[0]
        assert engine.findings(invoice, Syntax.UBL) == engine.findings(invoice, Syntax.UBL)


class TestFailureModes:
    def test_a_missing_file_is_reported_as_such(self, engine: ValidationEngine) -> None:
        with pytest.raises(ValidationError, match="no such document"):
            engine.findings(Path("/nonexistent/invoice.xml"), Syntax.UBL)

    def test_a_syntax_the_engine_was_not_built_for(self, real_ruleset: Ruleset) -> None:
        with (
            ValidationEngine(real_ruleset, syntaxes=[Syntax.UBL]) as partial,
            pytest.raises(ValidationError, match="not built for"),
        ):
            partial.findings(Path("whatever.xml"), Syntax.CII)

    def test_a_document_that_is_not_an_invoice_is_not_reported_clean(
        self, engine: ValidationEngine, tmp_path: Path
    ) -> None:
        """Well-formed XML that is not an invoice must not come back valid.

        Schematron alone cannot say "this is not an invoice" — that is the XSD
        layer's job, and it is not wired up yet. What it can do is refuse to call
        the document clean, which is the property worth pinning down now.
        """
        junk = tmp_path / "junk.xml"
        junk.write_text("<nonsense/>")
        findings = engine.findings(junk, Syntax.UBL)
        assert [f for f in findings if f.blocking]

    def test_malformed_xml_does_not_pass_silently(
        self, engine: ValidationEngine, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.xml"
        broken.write_text("<Invoice>")
        with pytest.raises(ValidationError):
            engine.findings(broken, Syntax.UBL)

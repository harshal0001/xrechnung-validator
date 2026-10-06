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

import re
from pathlib import Path

import pytest
from lxml import etree

from xrv.core import Finding, Severity, Syntax
from xrv.ingest import MalformedXmlError, identify
from xrv.rules import Ruleset
from xrv.validate import ValidationEngine, ValidationError

SYNTAX_GLOB = {Syntax.UBL: "*_ubl.xml", Syntax.CII: "*_uncefact.xml"}
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
#: A specification identifier no scenario claims.
UNRECOGNISED = b"urn:example:not-a-cius"
#: The standard's own identifier: an invoice that claims EN 16931 and no CIUS.
EN16931 = b"urn:cen.eu:en16931:2017"


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


def every_reference_message(corpus: Path) -> list[Path]:
    """The standard messages and the ones beside them: extension, CVD, technical cases."""
    return sorted(corpus.parent.rglob("*.xml"))


def customization_id(document: bytes) -> str:
    """BT-24 as the document carries it — read, never spelled here."""
    root = etree.fromstring(document)
    found = root.findtext(f"{{{CBC}}}CustomizationID") or root.findtext(
        f"{{{RSM}}}ExchangedDocumentContext/"
        f"{{{RAM}}}GuidelineSpecifiedDocumentContextParameter/{{{RAM}}}ID"
    )
    assert found, "the document carries no specification identifier"
    return found


def relabelled(document: bytes, identifier: bytes) -> bytes:
    """The same document, claiming to be something else."""
    return document.replace(customization_id(document).encode(), identifier)


def without_buyer_reference(document: bytes) -> bytes:
    stripped = re.sub(rb"<(\w+:)?BuyerReference>[^<]*</(\w+:)?BuyerReference>", b"", document)
    assert stripped != document
    return stripped


class TestScenarios:
    """A document is graded as the kind of document it says it is.

    KoSIT's configuration re-grades rules per scenario. The standard messages
    never show it, because nothing they trip is re-graded to or from blocking;
    the extension and CVD messages do, and were reported as invalid before the
    overrides were applied.
    """

    def test_no_reference_message_of_any_kind_is_rejected(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        messages = every_reference_message(corpus)
        assert len(messages) > len(list(corpus.glob("*.xml")))
        false_positives = {}
        for message in messages:
            document = message.read_bytes()
            blocking = [
                f for f in engine.findings(document, identify(document).syntax) if f.blocking
            ]
            if blocking:
                false_positives[message.name] = sorted({f.rule_id for f in blocking})
        assert not false_positives

    def test_every_reference_message_is_recognised(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        for message in every_reference_message(corpus):
            document = message.read_bytes()
            assert engine.evaluate(document, identify(document).syntax).scenario, message.name

    def test_the_kinds_are_told_apart(self, engine: ValidationEngine, corpus: Path) -> None:
        seen = set()
        for message in every_reference_message(corpus):
            document = message.read_bytes()
            scenario = engine.evaluate(document, identify(document).syntax).scenario
            assert scenario is not None
            seen.add(scenario.name)
        assert len(seen) >= 6

    def test_an_override_is_what_makes_an_extension_message_valid(
        self, engine: ValidationEngine, real_ruleset: Ruleset, corpus: Path
    ) -> None:
        """Not vacuous: the rule fires, the stylesheet calls it fatal, and the
        scenario is the only reason the document is not rejected."""
        downgraded = 0
        for message in sorted((corpus.parent / "extension").glob("*.xml")):
            document = message.read_bytes()
            evaluation = engine.evaluate(document, identify(document).syntax)
            assert evaluation.scenario is not None
            for finding in evaluation.findings:
                if evaluation.scenario.levels.get(finding.rule_id) is Severity.INFO:
                    assert finding.severity is Severity.INFO
                    downgraded += 1
        assert downgraded

    def test_an_override_can_also_make_a_rule_blocking(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        """Sub invoice lines are an extension feature. The same lines in a
        document that claims to be plain XRechnung are an error there, though
        the stylesheet only warns."""
        standard = customization_id((corpus / "01.01a-INVOICE_ubl.xml").read_bytes())
        upgraded: set[str] = set()
        for message in sorted((corpus.parent / "extension").glob("*_ubl.xml")):
            document = message.read_bytes()
            own = customization_id(document).encode()
            ungraded = engine.findings(document.replace(own, UNRECOGNISED), Syntax.UBL)
            as_standard = engine.findings(document.replace(own, standard.encode()), Syntax.UBL)
            warned = {f.rule_id for f in ungraded if not f.blocking}
            upgraded |= warned & {f.rule_id for f in as_standard if f.blocking}
        assert upgraded

    def test_every_stylesheet_a_scenario_names_is_one_the_rule_set_declares(
        self, real_ruleset: Ruleset
    ) -> None:
        """The engine refuses a document whose scenario names a stylesheet it
        does not hold. This is what keeps that refusal from reaching a user: a
        release that adds one fails here, when the rule set is adopted."""
        declared = {sheet for syntax in Syntax for sheet in real_ruleset.stylesheets(syntax)}
        for scenario in real_ruleset.scenarios:
            assert scenario.stylesheets, scenario.name
            assert {real_ruleset.root / s for s in scenario.stylesheets} <= declared, scenario.name

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_an_invoice_claiming_only_en16931_is_not_held_to_the_german_rules(
        self, engine: ValidationEngine, corpus: Path, syntax: Syntax
    ) -> None:
        """The same document, minus its buyer reference, under two claims. As an
        XRechnung it breaches the CIUS. As a plain EN 16931 invoice it breaches
        nothing, because the core does not ask for one."""
        document = without_buyer_reference(invoices(corpus, syntax)[0].read_bytes())
        as_xrechnung = engine.evaluate(document, syntax)
        as_core = engine.evaluate(relabelled(document, EN16931), syntax)

        assert [f.rule_id for f in as_xrechnung.findings if f.blocking] == ["BR-DE-15"]
        assert not as_xrechnung.skipped

        assert as_core.scenario is not None
        assert as_core.scenario.name != as_xrechnung.scenario.name
        assert not [f for f in as_core.findings if f.blocking]
        assert len(as_core.skipped) == 1

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_it_is_still_held_to_the_core(
        self, engine: ValidationEngine, corpus: Path, syntax: Syntax
    ) -> None:
        """Skipping the CIUS must not mean skipping validation."""
        document = relabelled(invoices(corpus, syntax)[0].read_bytes(), EN16931)
        currency = b"DocumentCurrencyCode>" if syntax is Syntax.UBL else b"InvoiceCurrencyCode>"
        assert currency + b"EUR<" in document
        broken = document.replace(currency + b"EUR<", currency + b"ZZZ<")
        fired = {f.rule_id for f in engine.evaluate(broken, syntax).findings if f.blocking}
        assert "BR-CL-04" in fired or "BR-CL-03" in fired

    def test_one_report_comes_back_per_stylesheet_that_ran(
        self, engine: ValidationEngine, real_ruleset: Ruleset, corpus: Path
    ) -> None:
        document = (corpus / "01.01a-INVOICE_ubl.xml").read_bytes()
        held = len(real_ruleset.stylesheets(Syntax.UBL))
        assert len(engine.reports(document, Syntax.UBL)) == held
        assert len(engine.reports(relabelled(document, EN16931), Syntax.UBL)) == held - 1
        assert all("schematron-output" in report for report in engine.reports(document, Syntax.UBL))

    def test_a_document_no_scenario_recognises_is_held_to_everything(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        """It has not said what it is, so nothing is assumed in its favour."""
        document = without_buyer_reference((corpus / "01.01a-INVOICE_ubl.xml").read_bytes())
        evaluation = engine.evaluate(relabelled(document, UNRECOGNISED), Syntax.UBL)
        assert evaluation.scenario is None
        assert not evaluation.skipped
        assert "BR-DE-15" in {f.rule_id for f in evaluation.findings if f.blocking}

    def test_a_document_no_scenario_recognises_keeps_the_stylesheets_grades(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        document = (corpus / "01.01a-INVOICE_ubl.xml").read_bytes()
        unknown = document.replace(customization_id(document).encode(), UNRECOGNISED)
        evaluation = engine.evaluate(unknown, Syntax.UBL)
        assert evaluation.scenario is None
        assert evaluation.findings == engine.rule_findings(unknown, Syntax.UBL)

    def test_a_structural_failure_is_not_given_a_scenario(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        """Nothing past the schema ran, so nothing was graded."""
        document = (corpus / "01.01a-INVOICE_ubl.xml").read_bytes()
        broken = document.replace(b"<cbc:ID>", b"<cbc:Oops>", 1).replace(
            b"</cbc:ID>", b"</cbc:Oops>", 1
        )
        evaluation = engine.evaluate(broken, Syntax.UBL)
        assert evaluation.scenario is None
        assert all(f.severity is Severity.FATAL for f in evaluation.findings)

    def test_the_rule_layer_alone_is_graded_the_same_way(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        for message in sorted((corpus.parent / "extension").glob("*.xml")):
            document = message.read_bytes()
            syntax = identify(document).syntax
            assert engine.rule_findings(document, syntax) == engine.findings(document, syntax)


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
            partial.rule_findings(Path("whatever.xml"), Syntax.CII)

    def test_a_document_that_is_not_an_invoice_is_named_as_such(
        self, engine: ValidationEngine, tmp_path: Path
    ) -> None:
        """The structural layer answers this directly, rather than by accident.

        Before the schema ran first, this document tripped an unrelated business
        rule about empty elements — technically not clean, but a misleading
        answer to give someone.
        """
        junk = tmp_path / "junk.xml"
        junk.write_text("<nonsense/>")
        (finding,) = engine.findings(junk, Syntax.UBL)
        assert finding.rule_id == "XSD-UNKNOWN-ROOT"
        assert finding.blocking

    def test_malformed_xml_does_not_pass_silently(
        self, engine: ValidationEngine, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.xml"
        broken.write_text("<Invoice>")
        with pytest.raises(MalformedXmlError):
            engine.findings(broken, Syntax.UBL)

    def test_the_rule_layer_alone_still_rejects_malformed_xml(
        self, engine: ValidationEngine, tmp_path: Path
    ) -> None:
        """rule_findings bypasses the schema, so it needs its own guard."""
        broken = tmp_path / "broken.xml"
        broken.write_text("<Invoice>")
        with pytest.raises(MalformedXmlError):
            engine.rule_findings(broken, Syntax.UBL)

    def test_bytes_and_a_path_give_the_same_report(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        invoice = corpus / "01.01a-INVOICE_ubl.xml"
        assert engine.findings(invoice, Syntax.UBL) == engine.findings(
            invoice.read_bytes(), Syntax.UBL
        )

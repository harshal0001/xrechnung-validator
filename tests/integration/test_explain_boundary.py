"""Can an explainer reach the invoice? It must not be able to.

The design claim is that a language model never sees the document. Until now
that was enforced only by a type signature, which is a good design and not
evidence. These tests are the evidence.

The strong one is `TestNothingLeaks`: it validates a real invoice carrying
distinctive strings — a company name, an IBAN, a customer's address — and
asserts that none of them appear anywhere in the findings handed to an
explainer, except in the one field that exists to carry the offending value.
That test would fail if someone later attached the source document to a Finding
"just for context", which is exactly how this kind of boundary erodes.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest
from lxml import etree

from xrv.core import Finding, Severity
from xrv.explain import (
    GROUNDING_FIELDS,
    Catalogue,
    CatalogueProvider,
    ExplanationProvider,
    NullProvider,
)
from xrv.ingest import identify
from xrv.validate import ValidationEngine

#: Values that appear in the reference invoice and would be damaging to leak.
#: Read out of the document rather than hardcoded, so the test keeps testing the
#: real content if the corpus changes.
SENSITIVE_PATHS = (
    ".//{*}PayeeFinancialAccount/{*}ID",
    ".//{*}AccountingSupplierParty//{*}RegistrationName",
    ".//{*}AccountingCustomerParty//{*}RegistrationName",
    ".//{*}AccountingCustomerParty//{*}StreetName",
    ".//{*}Item/{*}Name",
)


@pytest.fixture(scope="module")
def broken_invoice(corpus: Path, tmp_path_factory) -> bytes:
    """A real invoice, with one rule broken so findings actually exist."""
    tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
    root = tree.getroot()
    reference = root.find(
        "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}BuyerReference"
    )
    root.remove(reference)
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")


@pytest.fixture(scope="module")
def sensitive_values(corpus: Path) -> tuple[str, ...]:
    tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
    found = []
    for path in SENSITIVE_PATHS:
        for element in tree.findall(path):
            text = (element.text or "").strip()
            if len(text) > 4:
                found.append(text)
    assert found, "no sensitive values found — the test would prove nothing"
    return tuple(found)


@pytest.fixture(scope="module")
def findings(engine: ValidationEngine, broken_invoice: bytes) -> tuple[Finding, ...]:
    document = identify(broken_invoice)
    result = engine.findings(document.content, document.syntax)
    assert result, "no findings — nothing to check the boundary against"
    return result


class TestNothingLeaks:
    def test_no_invoice_content_reaches_a_finding(
        self, findings: tuple[Finding, ...], sensitive_values: tuple[str, ...]
    ) -> None:
        """The bank details, party names and line items stay in the document."""
        leaked: dict[str, list[str]] = {}
        for finding in findings:
            haystack = " ".join(
                str(getattr(finding, name) or "")
                for name in GROUNDING_FIELDS
                if name != "offending_value"
            )
            hits = [value for value in sensitive_values if value in haystack]
            if hits:
                leaked[finding.rule_id] = hits
        assert not leaked, f"invoice content reached findings: {leaked}"

    def test_a_finding_holds_no_reference_to_the_document(
        self, findings: tuple[Finding, ...]
    ) -> None:
        """Every field is a plain scalar, so there is nothing to traverse back
        to the tree, the file, or the bytes."""
        for finding in findings:
            for name in finding.__class__.model_fields:
                value = getattr(finding, name)
                assert isinstance(value, str | Severity | type(None)), (
                    f"{name} is a {type(value).__name__}, which may hold a reference "
                    f"back to the document"
                )

    def test_the_finding_carries_only_the_grounding_fields(self) -> None:
        """Widening this set has to be a deliberate, visible act.

        A field added to Finding is a field an explainer can read, so the model
        and the declared grounding set are asserted to agree.
        """
        assert set(Finding.model_fields) == GROUNDING_FIELDS | {"explanation"}


class TestTheExplainerIsGivenNothingElse:
    def test_the_provider_receives_a_finding_and_nothing_more(self) -> None:
        """No document, no path, no request context — one argument."""
        recorded: list[tuple] = []

        class Recorder:
            def explain(self, finding: Finding) -> str | None:
                recorded.append((finding,))
                return "erklärt"

        provider: ExplanationProvider = Recorder()
        finding = Finding(rule_id="BR-DE-15", severity=Severity.ERROR, rule_text="t", xpath="/x")
        provider.explain(finding)
        assert recorded == [(finding,)]

    def test_an_explainer_cannot_rewrite_what_it_was_grounded_in(self) -> None:
        """Attaching text produces a copy, so the rule text an explanation
        claims to be based on cannot be edited after the fact."""
        original = Finding(
            rule_id="BR-DE-15",
            severity=Severity.ERROR,
            rule_text="Das Element Buyer reference muss übermittelt werden.",
            xpath="/Invoice/cbc:BuyerReference",
        )
        explained = original.with_explanation("Die Käuferreferenz fehlt.")
        assert original.explanation is None
        assert explained.rule_text == original.rule_text

    def test_the_null_provider_satisfies_the_port(self) -> None:
        assert isinstance(NullProvider(), ExplanationProvider)


class TestCatalogueProviderInPractice:
    def test_it_explains_a_real_finding(self, findings: tuple[Finding, ...], catalogue) -> None:
        """End to end: a broken invoice comes back with German attached."""
        provider = CatalogueProvider(catalogue, require_reviewed=False)
        explained = provider.explained(findings)
        by_rule = {f.rule_id: f for f in explained}
        assert "BR-DE-15" in by_rule
        assert "Käuferreferenz" in (by_rule["BR-DE-15"].explanation or "")

    def test_it_holds_no_document_reference(self, catalogue: Catalogue) -> None:
        """The provider is a catalogue and two flags — no path, no bytes, no tree.

        A provider that held a file path or the parsed document could reach the
        invoice whatever its method signature said.
        """
        provider = CatalogueProvider(catalogue)
        held = {f.name: getattr(provider, f.name) for f in dataclass_fields(provider)}
        assert set(held) == {"catalogue", "require_reviewed", "require_current_text"}
        assert isinstance(held["catalogue"], Catalogue)
        assert all(
            isinstance(held[name], bool) for name in ("require_reviewed", "require_current_text")
        )

    def test_the_catalogue_itself_holds_only_text(self, catalogue: Catalogue) -> None:
        """Entries are strings and a flag. Nothing in the catalogue can be
        followed back to a document either."""
        for rule_id, entry in catalogue.entries.items():
            assert isinstance(rule_id, str)
            assert isinstance(entry.explanation, str)
            assert isinstance(entry.reviewed, bool)
            assert isinstance(entry.rule_text_digest, str)

    def test_findings_without_an_entry_are_returned_unchanged(
        self, findings: tuple[Finding, ...], catalogue
    ) -> None:
        """No explanation is a valid answer; the normative rule text remains."""
        provider = CatalogueProvider(catalogue, require_reviewed=False)
        for finding in provider.explained(findings):
            if finding.explanation is None:
                assert finding.rule_text

"""XSD validation against the official schemas.

The layer that answers "is this even an invoice?". Schematron cannot: it
evaluates rules against a shape it assumes exists, so pointed at arbitrary XML it
reports whatever happens to match rather than the fact that the document is the
wrong thing entirely.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from xrv.core import Severity, Syntax
from xrv.ingest import MalformedXmlError
from xrv.rules import Ruleset, RulesetNotFoundError
from xrv.validate import StructureValidator

UBL_NS = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
SYNTAX_GLOB = {Syntax.UBL: "*_ubl.xml", Syntax.CII: "*_uncefact.xml"}


@pytest.fixture(scope="module")
def structure(real_ruleset: Ruleset):
    with StructureValidator(real_ruleset) as built:
        yield built


def write(tmp_path: Path, tree: etree._ElementTree, name: str = "doc.xml") -> Path:
    target = tmp_path / name
    target.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
    return target


class TestValidDocumentsPass:
    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_every_reference_message_is_structurally_valid(
        self, structure: StructureValidator, corpus: Path, syntax: Syntax
    ) -> None:
        """A structural false positive would block a valid invoice outright."""
        invalid = {
            invoice.name: [f.rule_id for f in structure.findings(invoice, syntax)]
            for invoice in sorted(corpus.glob(SYNTAX_GLOB[syntax]))
        }
        assert not {k: v for k, v in invalid.items() if v}


class TestSchemaSelection:
    def test_both_ubl_document_types_have_a_schema(self, structure: StructureValidator) -> None:
        """UBL splits invoices and credit notes across two root elements, and the
        Schematron validates both. Judging a credit note against the invoice
        schema would reject a perfectly valid document."""
        assert "Invoice" in structure.roots
        assert "CreditNote" in structure.roots

    def test_the_ruleset_resolves_a_schema_per_root(self, real_ruleset: Ruleset) -> None:
        assert real_ruleset.xsd(Syntax.UBL, "Invoice").name == "UBL-Invoice-2.1.xsd"
        assert real_ruleset.xsd(Syntax.UBL, "CreditNote").name == "UBL-CreditNote-2.1.xsd"

    def test_an_unknown_root_is_refused_by_the_registry(self, real_ruleset: Ruleset) -> None:
        with pytest.raises(RulesetNotFoundError, match="Invoice, CreditNote"):
            real_ruleset.xsd(Syntax.UBL, "PurchaseOrder")


class TestStructuralFailures:
    def test_a_document_that_is_not_an_invoice(
        self, structure: StructureValidator, tmp_path: Path
    ) -> None:
        """The gap Schematron alone cannot close."""
        junk = tmp_path / "junk.xml"
        junk.write_text("<nonsense/>")
        (finding,) = structure.findings(junk, Syntax.UBL)
        assert finding.rule_id == "XSD-UNKNOWN-ROOT"
        assert finding.severity is Severity.FATAL

    def test_a_different_ubl_document_type(
        self, structure: StructureValidator, tmp_path: Path
    ) -> None:
        """Well-formed UBL, valid against its own schema, not an invoice."""
        order = tmp_path / "order.xml"
        order.write_text('<Order xmlns="urn:oasis:names:specification:ubl:schema:xsd:Order-2"/>')
        (finding,) = structure.findings(order, Syntax.UBL)
        assert finding.rule_id == "XSD-UNKNOWN-ROOT"
        assert "Order" in finding.rule_text

    def test_elements_out_of_order(
        self, structure: StructureValidator, corpus: Path, tmp_path: Path
    ) -> None:
        """UBL element order is part of the schema, not a convention."""
        tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
        root = tree.getroot()
        root.insert(0, root.find("cbc:IssueDate", UBL_NS))
        findings = structure.findings(write(tmp_path, tree), Syntax.UBL)
        assert findings
        assert all(f.severity is Severity.FATAL for f in findings)

    def test_a_value_of_the_wrong_type(
        self, structure: StructureValidator, corpus: Path, tmp_path: Path
    ) -> None:
        tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
        tree.getroot().find("cbc:IssueDate", UBL_NS).text = "not-a-date"
        (finding, *_) = structure.findings(write(tmp_path, tree), Syntax.UBL)
        assert finding.severity is Severity.FATAL
        # The message quotes the offending value, which is what an explanation
        # would otherwise have to be told separately.
        assert "not-a-date" in finding.rule_text

    def test_an_element_the_schema_does_not_define(
        self, structure: StructureValidator, corpus: Path, tmp_path: Path
    ) -> None:
        tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
        etree.SubElement(tree.getroot(), f"{{{UBL_NS['cbc']}}}Invented")
        assert structure.findings(write(tmp_path, tree), Syntax.UBL)


class TestFindingsAreUsable:
    @pytest.fixture(scope="class")
    @classmethod
    def broken(cls, structure: StructureValidator, corpus: Path, tmp_path_factory):
        tree = etree.parse(str(corpus / "01.01a-INVOICE_ubl.xml"))
        root = tree.getroot()
        root.insert(0, root.find("cbc:IssueDate", UBL_NS))
        target = tmp_path_factory.mktemp("structure") / "reordered.xml"
        target.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
        return structure.findings(target, Syntax.UBL)

    def test_rule_ids_name_the_layer(self, broken) -> None:
        """A mixed report should show at a glance which layer produced a finding."""
        assert all(f.rule_id.startswith("XSD-") for f in broken)

    def test_every_finding_carries_text(self, broken) -> None:
        assert all(f.rule_text.strip() for f in broken)

    def test_every_finding_carries_a_location(self, broken) -> None:
        assert all(f.xpath.strip() for f in broken)

    def test_structural_failures_are_fatal(self, broken) -> None:
        assert all(f.severity is Severity.FATAL for f in broken)
        assert all(f.blocking for f in broken)


class TestRejects:
    def test_malformed_xml_is_not_a_finding(
        self, structure: StructureValidator, tmp_path: Path
    ) -> None:
        """A document that does not parse is not a document to report on."""
        broken = tmp_path / "broken.xml"
        broken.write_text("<Invoice>")
        with pytest.raises(MalformedXmlError, match="not well-formed"):
            structure.findings(broken, Syntax.UBL)

    def test_a_missing_file(self, structure: StructureValidator, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            structure.findings(tmp_path / "absent.xml", Syntax.UBL)

    def test_bytes_and_a_path_give_the_same_answer(
        self, structure: StructureValidator, corpus: Path
    ) -> None:
        """In production the document arrives as an upload, not a file."""
        invoice = corpus / "01.01a-INVOICE_ubl.xml"
        assert structure.findings(invoice, Syntax.UBL) == structure.findings(
            invoice.read_bytes(), Syntax.UBL
        )

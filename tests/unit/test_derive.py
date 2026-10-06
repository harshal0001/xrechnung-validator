"""Reading rules and editing documents, without Saxon and without a rule set.

The search that confirms a mutation needs the real stylesheets. What it is fed
does not: which elements a rule reads, and what an edit does to a document, are
both decided here, on small inline inputs written in the shapes the compiled
KoSIT stylesheets actually use.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

from mutation.catalogue import MutationError
from mutation.derive import (
    NAMESPACES,
    Derived,
    DocumentIndex,
    Edit,
    Op,
    Shape,
    classify,
    expand,
    literals,
    name_suffixes,
    newly_fired,
    plans,
    read_expression,
    read_stylesheet,
)
from xrv.core import Syntax

CBC = NAMESPACES["cbc"]
CAC = NAMESPACES["cac"]


def paths(expression: str) -> list[str]:
    walked, _ = read_expression(expression)
    return ["/".join(chain.steps) for chain in walked]


def in_predicates(expression: str) -> list[str]:
    _, inside = read_expression(expression)
    return ["/".join(chain.steps) for chain in inside]


class TestReadingAnExpression:
    def test_a_bare_path_is_one_chain(self) -> None:
        assert paths("cac:Contact/cbc:Telephone") == ["cac:Contact/cbc:Telephone"]

    def test_a_cast_in_the_middle_of_a_path_does_not_split_it(self) -> None:
        """`a/xs:decimal(b)` is a path to `b`. Read as two, `b` loses its parent
        and matches every element of that name in the document."""
        assert paths("cac:LegalMonetaryTotal/xs:decimal(cbc:PayableAmount) = 0") == [
            "cac:LegalMonetaryTotal/cbc:PayableAmount"
        ]

    def test_nested_value_functions_are_seen_through(self) -> None:
        assert paths("cac:TaxScheme/normalize-space(upper-case(cbc:ID)) = 'VAT'") == [
            "cac:TaxScheme/cbc:ID"
        ]

    def test_function_names_are_not_elements(self) -> None:
        assert paths("xs:decimal(cbc:Amount) >= u:floor(cbc:BaseAmount, 2)") == [
            "cbc:Amount",
            "cbc:BaseAmount",
        ]

    def test_a_predicate_is_read_against_the_step_it_hangs_on(self) -> None:
        expression = "cac:TaxCategory[normalize-space(cbc:ID) = 'S']/cbc:Percent"
        assert paths(expression) == ["cac:TaxCategory/cbc:Percent"]
        assert in_predicates(expression) == ["cac:TaxCategory/cbc:ID"]

    def test_an_attribute_predicate_keeps_its_element(self) -> None:
        assert in_predicates("cbc:TaxAmount[@currencyID = $Currency]") == [
            "cbc:TaxAmount/@currencyID"
        ]

    def test_stacked_predicates_share_one_owner(self) -> None:
        assert in_predicates("cac:TaxCategory[cbc:ID = 'S'][cac:TaxScheme/cbc:ID = 'VAT']") == [
            "cac:TaxCategory/cbc:ID",
            "cac:TaxCategory/cac:TaxScheme/cbc:ID",
        ]

    def test_brackets_inside_a_literal_are_not_a_predicate(self) -> None:
        expression = "matches(cbc:CompanyID, '^[A-Z]{2}[0-9]+$')"
        assert paths(expression) == ["cbc:CompanyID"]
        assert in_predicates(expression) == []

    @pytest.mark.parametrize(
        "expression",
        [
            "/ubl:Invoice/cbc:ID",
            "//cac:PostalAddress",
            "../cbc:DocumentCurrencyCode",
            "ancestor::cac:AllowanceCharge/cbc:ChargeIndicator",
        ],
    )
    def test_an_anchored_path_is_not_relative_to_the_context(self, expression: str) -> None:
        """Joining it onto the context would describe a node that is not there."""
        (walked,), _ = read_expression(expression)
        assert not walked.relative

    def test_a_plain_path_is_relative(self) -> None:
        (walked,), _ = read_expression("exists(cbc:BuyerReference)")
        assert walked.relative


class TestVariables:
    def test_a_reference_is_replaced_by_what_it_selects(self) -> None:
        expanded = expand("$seller = ''", {"seller": ("cac:Party/cbc:Name",)})
        assert paths(expanded) == ["cac:Party/cbc:Name"]

    def test_references_are_followed_through_each_other(self) -> None:
        variables = {"a": ("$b/cbc:ID",), "b": ("cac:Party",)}
        assert "cac:Party" in expand("$a", variables)

    def test_a_name_bound_by_the_expression_itself_is_left_alone(self) -> None:
        expression = "every $line in cac:InvoiceLine satisfies $line/cbc:ID"
        assert expand(expression, {}) == expression

    def test_a_variable_that_selects_itself_terminates(self) -> None:
        assert expand("$a", {"a": ("$a",)})


class TestLiterals:
    def test_short_values_are_kept_in_order(self) -> None:
        assert literals("cbc:ID = 'S' or cbc:ID = \"AE\" or cbc:ID = 'S'") == ("S", "AE")

    def test_a_code_list_spelled_as_one_string_is_not_a_value(self) -> None:
        assert literals("contains(' 380 381 384 389 ', concat(' ', cbc:TypeCode, ' '))") == ()

    def test_a_name_suffix_is_read_from_a_context_with_no_path(self) -> None:
        assert name_suffixes("//*[ends-with(name(), 'Amount')]") == ("Amount",)


class TestShape:
    @pytest.mark.parametrize(
        ("test", "shape"),
        [
            ("exists(cbc:ID)", Shape.PRESENCE),
            ("normalize-space(cbc:ID) != ''", Shape.PRESENCE),
            ("cac:PaymentMeans", Shape.PRESENCE),
            ("not(cac:Delivery)", Shape.PROHIBITION),
            ("count(cbc:Note) <= 1", Shape.PROHIBITION),
            ("matches(cbc:ID, '^[A-Z]+$')", Shape.PATTERN),
            ("xs:decimal(cbc:A) = round(sum(cac:Line/xs:decimal(cbc:B)) * 100)", Shape.ARITHMETIC),
            ("xs:decimal(cbc:Percent) > 0", Shape.COMPARISON),
            (
                "contains(' 380 381 384 389 751 ', concat(' ', normalize-space(.), ' '))",
                Shape.CODE_LIST,
            ),
        ],
    )
    def test_is_read_from_the_test(self, test: str, shape: Shape) -> None:
        assert classify(test) is shape


INVOICE = f"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="{CBC}" xmlns:cac="{CAC}">
  <cbc:ID>R-1</cbc:ID>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PostalAddress><cbc:CityName>Köln</cbc:CityName></cac:PostalAddress>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PostalAddress><cbc:CityName>Bonn</cbc:CityName></cac:PostalAddress>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">19.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:InvoiceLine><cbc:ID>1</cbc:ID></cac:InvoiceLine>
  <cac:InvoiceLine><cbc:ID>2</cbc:ID></cac:InvoiceLine>
</Invoice>""".encode()

SELLER_CITY = "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:CityName"


def text_at(document: bytes, path: str) -> list[str | None]:
    root = etree.fromstring(document)
    return [node.text for node in root.findall(path, namespaces=NAMESPACES)]


class TestEdits:
    def test_delete_removes_the_element(self) -> None:
        edited = Edit(SELLER_CITY, Op.DELETE).apply(INVOICE)
        assert text_at(edited, SELLER_CITY) == []

    def test_delete_leaves_the_rest_alone(self) -> None:
        edited = Edit(SELLER_CITY, Op.DELETE).apply(INVOICE)
        buyer = "cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:CityName"
        assert text_at(edited, buyer) == ["Bonn"]

    def test_set_replaces_the_text(self) -> None:
        edited = Edit("cbc:DocumentCurrencyCode", Op.SET, value="ZZZ").apply(INVOICE)
        assert text_at(edited, "cbc:DocumentCurrencyCode") == ["ZZZ"]

    def test_duplicate_repeats_the_element_in_place(self) -> None:
        edited = Edit("cac:TaxTotal", Op.DUPLICATE).apply(INVOICE)
        root = etree.fromstring(edited)
        names = [etree.QName(child).localname for child in root]
        assert names.count("TaxTotal") == 2
        assert names.index("TaxTotal") + 1 == len(names) - 1 - names[::-1].index("TaxTotal")

    def test_an_attribute_can_be_set_and_removed(self) -> None:
        amount = "cac:TaxTotal/cbc:TaxAmount"
        changed = Edit(amount, Op.SET, "currencyID", "USD").apply(INVOICE)
        removed = Edit(amount, Op.DELETE, "currencyID").apply(INVOICE)
        assert etree.fromstring(changed).find(amount, NAMESPACES).get("currencyID") == "USD"
        assert etree.fromstring(removed).find(amount, NAMESPACES).get("currencyID") is None

    def test_a_position_selects_one_of_several(self) -> None:
        edited = Edit("cac:InvoiceLine[2]/cbc:ID", Op.SET, value="9").apply(INVOICE)
        assert text_at(edited, "cac:InvoiceLine/cbc:ID") == ["1", "9"]

    @pytest.mark.parametrize(
        "edit",
        [
            Edit("cbc:DocumentCurrencyCode", Op.SET, value="EUR"),
            Edit("cac:TaxTotal/cbc:TaxAmount", Op.SET, "currencyID", "EUR"),
            Edit("cac:TaxTotal", Op.SET, value="x"),
        ],
    )
    def test_an_edit_that_changes_nothing_is_an_error(self, edit: Edit) -> None:
        """Otherwise the test built on it asserts things about a valid invoice."""
        with pytest.raises(MutationError, match="changes nothing"):
            edit.apply(INVOICE)

    def test_a_path_that_matches_nothing_is_an_error(self) -> None:
        with pytest.raises(MutationError, match="matched nothing"):
            Edit("cbc:NoSuchElement", Op.DELETE).apply(INVOICE)

    def test_a_missing_attribute_is_an_error(self) -> None:
        with pytest.raises(MutationError, match="no attribute"):
            Edit("cbc:ID", Op.DELETE, "schemeID").apply(INVOICE)


def stylesheet(tmp_path: Path, *templates: str) -> Path:
    """A file in the shape a compiled Schematron has, holding only what is read."""
    path = tmp_path / "rules.xsl"
    path.write_text(
        '<xsl:transform xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'xmlns:svrl="http://purl.oclc.org/dsdl/svrl" '
        'xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        f'xmlns:cbc="{CBC}" xmlns:cac="{CAC}" version="2.0">'
        "<xsl:variable name=\"codes\" select=\"('S', 'AE')\"/>"
        + "".join(templates)
        + "</xsl:transform>",
        encoding="utf-8",
    )
    return path


def template(context: str, rule_id: str, test: str) -> str:
    return (
        f'<xsl:template match="{context}"><xsl:if test="not({test})">'
        f'<svrl:failed-assert flag="fatal" id="{rule_id}">'
        f'<xsl:attribute name="test">{test}</xsl:attribute>'
        f"<svrl:text>[{rule_id}] text</svrl:text>"
        "</svrl:failed-assert></xsl:if></xsl:template>"
    )


class TestReadingAStylesheet:
    def test_each_assertion_carries_its_template_and_test(self, tmp_path: Path) -> None:
        sheet = read_stylesheet(
            stylesheet(tmp_path, template("/ubl:Invoice", "BR-X-1", "cbc:BuyerReference"))
        )
        (assertion,) = sheet.assertions
        assert (assertion.rule_id, assertion.flag) == ("BR-X-1", "fatal")
        assert assertion.context == "/ubl:Invoice"
        assert assertion.test == "cbc:BuyerReference"

    def test_variables_and_prefixes_are_collected(self, tmp_path: Path) -> None:
        sheet = read_stylesheet(stylesheet(tmp_path))
        assert sheet.variables["codes"] == ("('S', 'AE')",)
        assert sheet.namespaces["cbc"] == CBC

    def test_syntax_binding_rules_are_not_planned(self, tmp_path: Path) -> None:
        sheet = read_stylesheet(
            stylesheet(
                tmp_path,
                template("/ubl:Invoice", "UBL-CR-001", "not(cbc:UBLVersionID)"),
                template("/ubl:Invoice", "BR-X-1", "cbc:ID"),
            )
        )
        assert set(plans([sheet])) == {"BR-X-1"}

    def test_assertions_sharing_an_id_are_one_plan(self, tmp_path: Path) -> None:
        sheet = read_stylesheet(
            stylesheet(
                tmp_path,
                template("/ubl:Invoice", "BR-X-1", "cbc:ID"),
                template("cac:InvoiceLine", "BR-X-1", "cbc:ID"),
            )
        )
        (plan,) = plans([sheet]).values()
        assert plan.contexts == {"/ubl:Invoice", "cac:InvoiceLine"}


class TestProposingEdits:
    def edits(self, tmp_path: Path, context: str, test: str) -> set[Edit]:
        sheet = read_stylesheet(stylesheet(tmp_path, template(context, "BR-X-1", test)))
        (plan,) = plans([sheet]).values()
        return set(DocumentIndex(INVOICE).edits(plan))

    def test_the_context_decides_which_of_two_like_named_nodes(self, tmp_path: Path) -> None:
        """Seller and buyer city are the same path under different parties."""
        found = self.edits(
            tmp_path, "cac:AccountingSupplierParty/cac:Party", "cac:PostalAddress/cbc:CityName"
        )
        assert Edit(SELLER_CITY, Op.DELETE) in found
        assert not [e for e in found if "AccountingCustomerParty" in e.path]

    def test_the_enclosing_element_the_rule_names_is_removable_too(self, tmp_path: Path) -> None:
        found = self.edits(
            tmp_path, "cac:AccountingSupplierParty/cac:Party", "cac:PostalAddress/cbc:CityName"
        )
        address = "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress"
        assert Edit(address, Op.DELETE) in found

    def test_a_group_is_never_given_text(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cac:TaxTotal")
        assert {e.op for e in found if e.path == "cac:TaxTotal"} == {Op.DELETE, Op.DUPLICATE}

    def test_a_number_is_tried_with_wrong_numbers(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cac:TaxTotal/xs:decimal(cbc:TaxAmount) >= 0")
        values = {e.value for e in found if e.path.endswith("TaxAmount") and e.op is Op.SET}
        assert {"999.99", "-1", "1.234"} <= values
        assert "ZZZ" not in values

    def test_the_rules_own_literals_are_tried(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cbc:DocumentCurrencyCode = 'USD'")
        assert Edit("cbc:DocumentCurrencyCode", Op.SET, value="USD") in found

    def test_a_literal_behind_a_variable_is_tried(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cbc:DocumentCurrencyCode = $codes")
        assert Edit("cbc:DocumentCurrencyCode", Op.SET, value="AE") in found

    def test_the_value_already_there_is_not_proposed(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cbc:DocumentCurrencyCode = 'EUR'")
        assert Edit("cbc:DocumentCurrencyCode", Op.SET, value="EUR") not in found

    def test_an_attribute_the_rule_reads_is_a_target(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cac:TaxTotal/cbc:TaxAmount/@currencyID")
        assert Edit("cac:TaxTotal/cbc:TaxAmount", Op.DELETE, "currencyID") in found

    def test_a_path_that_leaves_the_context_still_finds_its_node(self, tmp_path: Path) -> None:
        """The join onto the context matches nothing, so the path is tried alone."""
        found = self.edits(tmp_path, "cac:InvoiceLine", "../cbc:DocumentCurrencyCode")
        assert Edit("cbc:DocumentCurrencyCode", Op.DELETE) in found

    def test_the_root_element_is_never_a_target(self, tmp_path: Path) -> None:
        found = self.edits(tmp_path, "/ubl:Invoice", "cbc:ID")
        assert all(e.path != "." for e in found)

    def test_every_proposed_edit_applies(self, tmp_path: Path) -> None:
        for edit in self.edits(tmp_path, "/ubl:Invoice", "cac:InvoiceLine/cbc:ID = '1'"):
            assert edit.apply(INVOICE) != INVOICE


class TestResults:
    def derived(self, **changes: object) -> Derived:
        fields: dict[str, object] = {
            "rule_id": "BR-X-1",
            "syntax": Syntax.UBL,
            "base": "standard/a.xml",
            "edit": Edit("cbc:ID", Op.DELETE),
        }
        return Derived(**{**fields, **changes})  # type: ignore[arg-type]

    def test_survives_the_file(self) -> None:
        full = self.derived(
            edit=Edit("cbc:Amount", Op.SET, "currencyID", ""),
            collateral=frozenset({"BR-CO-15", "BR-CO-13"}),
            caught_by_schema=True,
        )
        for mutation in (self.derived(), full):
            assert Derived.from_json(mutation.to_json()) == mutation

    def test_passing_the_schema_beats_everything(self) -> None:
        caught = self.derived(caught_by_schema=True)
        noisy = self.derived(collateral=frozenset({"A", "B", "C"}))
        assert noisy.rank() < caught.rank()

    def test_less_collateral_beats_a_plainer_edit(self) -> None:
        alone = self.derived(edit=Edit("cbc:ID", Op.SET, value="ZZZ"))
        noisy = self.derived(collateral=frozenset({"A"}))
        assert alone.rank() < noisy.rank()

    def test_edits_on_and_off_an_attribute_can_be_ranked(self) -> None:
        """One has an attribute name and the other None; the key must not compare them."""
        plain = self.derived()
        attribute = self.derived(edit=Edit("cbc:ID", Op.DELETE, "schemeID"))
        assert sorted([attribute, plain], key=Derived.rank) == [plain, attribute]

    def test_a_rule_firing_once_more_than_before_has_newly_fired(self) -> None:
        before = Counter({"BR-A": 1, "BR-B": 2})
        after = Counter({"BR-A": 2, "BR-B": 2, "BR-C": 1})
        assert newly_fired(before, after) == {"BR-A", "BR-C"}

    def test_a_rule_that_stopped_firing_is_not_reported(self) -> None:
        assert newly_fired(Counter({"BR-A": 1}), Counter()) == frozenset()

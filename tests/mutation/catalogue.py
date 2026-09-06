"""Mutations that break exactly one business rule.

The KoSIT reference messages are valid by construction. Passing them proves the
service emits no false positives; it proves nothing about whether a broken
invoice is caught. This is the other half: take a valid document, break one thing,
and assert the rule that should fire does — and that nothing else blocking does.

Each mutation names the rules it is *allowed* to trip. Usually that is one. Some
breakages unavoidably trip several — removing a seller contact group takes the
group rule and each of its child-element rules with it — and declaring that is
more honest than pretending otherwise or picking only the convenient rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from lxml import etree

from xrv.core import Syntax

NAMESPACES = {
    # UBL 2.1
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    # UN/CEFACT CII 16B
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


class MutationError(RuntimeError):
    """The mutation did not apply, so the test built on it would prove nothing."""


class Change(StrEnum):
    """How a mutation breaks the document.

    Presence rules are broken by removing a required element. Calculation rules
    cannot be: deleting a total breaks the rule that requires it rather than the
    rule that checks it adds up. Those need a value that is present, well-formed
    and wrong.
    """

    DELETE = "delete"
    SET_TEXT = "set-text"


@dataclass(frozen=True)
class Mutation:
    """One deliberate breakage of a valid invoice."""

    rule_id: str
    syntax: Syntax
    breaks: str
    xpath: str
    change: Change = Change.DELETE
    #: Replacement text, for SET_TEXT. Chosen to stay a valid amount so the
    #: schema still accepts the document and the arithmetic rule is what fails.
    value: str | None = None
    #: Rules that this breakage unavoidably trips as well as `rule_id`.
    collateral: frozenset[str] = field(default_factory=frozenset)
    #: True when the XSD also rejects this document. Some EN 16931 rules restate
    #: a constraint the schema already enforces, so the integrated pipeline stops
    #: at the structural failure and the business rule never runs. Recording which
    #: ones overlap is the point — the rule is still proven, at its own layer.
    caught_by_schema: bool = False

    @property
    def expected(self) -> frozenset[str]:
        return self.collateral | {self.rule_id}

    @property
    def name(self) -> str:
        return f"{self.rule_id}-{self.syntax}"

    def apply(self, invoice: Path) -> bytes:
        """Return the document with this mutation applied.

        Raises when the mutation would be a no-op. That check is the point: a
        mutation that silently fails to mutate turns its test into an assertion
        about a valid document, which passes for the wrong reason.
        """
        tree = etree.parse(str(invoice))
        targets = tree.xpath(self.xpath, namespaces=NAMESPACES)
        if not targets:
            raise MutationError(
                f"{self.name}: {self.xpath!r} matched nothing in {invoice.name} — "
                f"the reference message may have changed shape"
            )

        if self.change is Change.DELETE:
            self._delete(targets)
        else:
            self._set_text(targets, invoice)
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

    def _delete(self, targets: list) -> None:
        for node in targets:
            parent = node.getparent()
            if parent is None:
                raise MutationError(f"{self.name}: refusing to remove the root element")
            parent.remove(node)

    def _set_text(self, targets: list, invoice: Path) -> None:
        if self.value is None:
            raise MutationError(f"{self.name}: SET_TEXT needs a value")
        for node in targets:
            if (node.text or "").strip() == self.value:
                raise MutationError(
                    f"{self.name}: {self.xpath!r} already reads {self.value!r} in "
                    f"{invoice.name}, so this mutation changes nothing"
                )
            node.text = self.value


_SELLER = "/*/cac:AccountingSupplierParty/cac:Party"
_BUYER = "/*/cac:AccountingCustomerParty/cac:Party"
_CII_AGREEMENT = "/*/rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement"
_CII_SELLER = f"{_CII_AGREEMENT}/ram:SellerTradeParty"
_CII_BUYER = f"{_CII_AGREEMENT}/ram:BuyerTradeParty"
_CII_SETTLEMENT = "/*/rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
_CII_TOTALS = f"{_CII_SETTLEMENT}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
_UBL_TOTALS = "/*/cac:LegalMonetaryTotal"

#: Well-formed, plausible, and not the right number. The schema still accepts it,
#: so the arithmetic rule is what fails rather than the type check.
_WRONG_AMOUNT = "999.99"

#: Every mutation is a deletion. Deleting a mandatory element is the cleanest way
#: to breach a presence rule without accidentally breaching a format rule too.
MUTATIONS: tuple[Mutation, ...] = (
    # ---- UBL: German CIUS rules ---------------------------------------------
    Mutation(
        rule_id="BR-DE-15",
        syntax=Syntax.UBL,
        breaks="Buyer reference (BT-10) removed — routes an invoice to a cost centre",
        xpath="/*/cbc:BuyerReference",
    ),
    Mutation(
        rule_id="BR-DE-1",
        syntax=Syntax.UBL,
        breaks="Payment instructions (BG-16) removed",
        xpath="/*/cac:PaymentMeans",
    ),
    Mutation(
        rule_id="BR-DE-2",
        syntax=Syntax.UBL,
        breaks="Seller contact group (BG-6) removed entirely",
        xpath=f"{_SELLER}/cac:Contact",
    ),
    Mutation(
        rule_id="BR-DE-3",
        syntax=Syntax.UBL,
        breaks="Seller city (BT-37) removed",
        xpath=f"{_SELLER}/cac:PostalAddress/cbc:CityName",
    ),
    Mutation(
        rule_id="BR-DE-4",
        syntax=Syntax.UBL,
        breaks="Seller post code (BT-38) removed",
        xpath=f"{_SELLER}/cac:PostalAddress/cbc:PostalZone",
    ),
    Mutation(
        rule_id="BR-DE-6",
        syntax=Syntax.UBL,
        breaks="Seller contact telephone (BT-42) removed",
        xpath=f"{_SELLER}/cac:Contact/cbc:Telephone",
    ),
    Mutation(
        rule_id="BR-DE-7",
        syntax=Syntax.UBL,
        breaks="Seller contact email (BT-43) removed",
        xpath=f"{_SELLER}/cac:Contact/cbc:ElectronicMail",
    ),
    Mutation(
        rule_id="BR-DE-8",
        syntax=Syntax.UBL,
        breaks="Buyer city (BT-52) removed",
        xpath=f"{_BUYER}/cac:PostalAddress/cbc:CityName",
    ),
    Mutation(
        rule_id="BR-DE-9",
        syntax=Syntax.UBL,
        breaks="Buyer post code (BT-53) removed",
        xpath=f"{_BUYER}/cac:PostalAddress/cbc:PostalZone",
    ),
    # ---- UBL: EN 16931 core rules -------------------------------------------
    Mutation(
        rule_id="BR-02",
        syntax=Syntax.UBL,
        caught_by_schema=True,
        breaks="Invoice number (BT-1) removed",
        xpath="/*/cbc:ID",
    ),
    Mutation(
        rule_id="BR-03",
        syntax=Syntax.UBL,
        caught_by_schema=True,
        breaks="Issue date (BT-2) removed",
        xpath="/*/cbc:IssueDate",
    ),
    Mutation(
        rule_id="BR-05",
        syntax=Syntax.UBL,
        breaks="Invoice currency code (BT-5) removed",
        xpath="/*/cbc:DocumentCurrencyCode",
    ),
    Mutation(
        rule_id="BR-08",
        syntax=Syntax.UBL,
        breaks="Seller postal address (BG-5) removed entirely",
        xpath=f"{_SELLER}/cac:PostalAddress",
    ),
    Mutation(
        rule_id="BR-10",
        syntax=Syntax.UBL,
        breaks="Buyer postal address (BG-8) removed entirely",
        xpath=f"{_BUYER}/cac:PostalAddress",
    ),
    Mutation(
        rule_id="BR-16",
        syntax=Syntax.UBL,
        caught_by_schema=True,
        breaks="Every invoice line removed",
        # A document with no lines cannot have a consistent line total or VAT
        # breakdown either, so those rules fire too. Declared rather than hidden.
        collateral=frozenset({"BR-CO-10", "BR-S-01", "BR-S-08"}),
        xpath="/*/cac:InvoiceLine",
    ),
    # ---- CII ----------------------------------------------------------------
    Mutation(
        rule_id="BR-DE-15",
        syntax=Syntax.CII,
        breaks="Buyer reference (BT-10) removed",
        xpath=f"{_CII_AGREEMENT}/ram:BuyerReference",
    ),
    Mutation(
        rule_id="BR-DE-3",
        syntax=Syntax.CII,
        breaks="Seller city (BT-37) removed",
        xpath=f"{_CII_SELLER}/ram:PostalTradeAddress/ram:CityName",
    ),
    Mutation(
        rule_id="BR-DE-4",
        syntax=Syntax.CII,
        breaks="Seller post code (BT-38) removed",
        xpath=f"{_CII_SELLER}/ram:PostalTradeAddress/ram:PostcodeCode",
    ),
    Mutation(
        rule_id="BR-DE-8",
        syntax=Syntax.CII,
        breaks="Buyer city (BT-52) removed",
        xpath=f"{_CII_BUYER}/ram:PostalTradeAddress/ram:CityName",
    ),
    Mutation(
        rule_id="BR-DE-9",
        syntax=Syntax.CII,
        breaks="Buyer post code (BT-53) removed",
        xpath=f"{_CII_BUYER}/ram:PostalTradeAddress/ram:PostcodeCode",
    ),
    Mutation(
        rule_id="BR-02",
        syntax=Syntax.CII,
        caught_by_schema=True,
        breaks="Invoice number (BT-1) removed",
        xpath="/*/rsm:ExchangedDocument/ram:ID",
    ),
    # ---- Calculation rules --------------------------------------------------
    # These cannot be reached by deletion. Removing a total breaks the rule that
    # requires it, not the rule that checks it adds up; the arithmetic rules need
    # a value that is present, well-formed, and wrong. Each total appears in more
    # than one equation, so corrupting one legitimately breaks two — declared as
    # collateral rather than hidden behind a looser assertion.
    Mutation(
        rule_id="BR-CO-10",
        syntax=Syntax.UBL,
        breaks="Sum of line net amounts (BT-106) no longer matches the lines",
        xpath=f"{_UBL_TOTALS}/cbc:LineExtensionAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-13"}),
    ),
    Mutation(
        rule_id="BR-CO-13",
        syntax=Syntax.UBL,
        breaks="Total without VAT (BT-109) no longer matches lines plus adjustments",
        xpath=f"{_UBL_TOTALS}/cbc:TaxExclusiveAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-15"}),
    ),
    Mutation(
        rule_id="BR-CO-14",
        syntax=Syntax.UBL,
        breaks="VAT total (BT-110) no longer matches the VAT breakdown",
        xpath="/*/cac:TaxTotal/cbc:TaxAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-15"}),
    ),
    Mutation(
        rule_id="BR-CO-15",
        syntax=Syntax.UBL,
        breaks="Total with VAT (BT-112) is not the net total plus VAT",
        xpath=f"{_UBL_TOTALS}/cbc:TaxInclusiveAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-16"}),
    ),
    Mutation(
        rule_id="BR-CO-16",
        syntax=Syntax.UBL,
        breaks="Amount due (BT-115) is not the gross total minus what was prepaid",
        xpath=f"{_UBL_TOTALS}/cbc:PayableAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
    ),
    Mutation(
        rule_id="BR-CO-10",
        syntax=Syntax.CII,
        breaks="Sum of line net amounts no longer matches the lines",
        xpath=f"{_CII_TOTALS}/ram:LineTotalAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-13"}),
    ),
    Mutation(
        rule_id="BR-CO-13",
        syntax=Syntax.CII,
        breaks="Tax basis total no longer matches lines plus adjustments",
        xpath=f"{_CII_TOTALS}/ram:TaxBasisTotalAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-15"}),
    ),
    Mutation(
        rule_id="BR-CO-15",
        syntax=Syntax.CII,
        breaks="Grand total is not the tax basis plus VAT",
        xpath=f"{_CII_TOTALS}/ram:GrandTotalAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
        collateral=frozenset({"BR-CO-16"}),
    ),
    Mutation(
        rule_id="BR-CO-16",
        syntax=Syntax.CII,
        breaks="Amount due is not the grand total minus what was prepaid",
        xpath=f"{_CII_TOTALS}/ram:DuePayableAmount",
        change=Change.SET_TEXT,
        value=_WRONG_AMOUNT,
    ),
    # ---- Code list and content rules ----------------------------------------
    # Also unreachable by deletion: the value has to be present and wrong, not
    # absent. A code list rule fires when a field holds something that is not in
    # the list the standard points at.
    Mutation(
        rule_id="BR-CL-01",
        syntax=Syntax.UBL,
        breaks="Invoice type code (BT-3) is not one the standard allows",
        xpath="/*/cbc:InvoiceTypeCode",
        change=Change.SET_TEXT,
        value="999",
    ),
    Mutation(
        rule_id="BR-CL-04",
        syntax=Syntax.UBL,
        breaks="Invoice currency (BT-5) is not an ISO 4217 code",
        xpath="/*/cbc:DocumentCurrencyCode",
        change=Change.SET_TEXT,
        value="ZZZ",
        collateral=frozenset({"BR-CO-15"}),
    ),
    Mutation(
        rule_id="BR-CL-14",
        syntax=Syntax.UBL,
        breaks="Seller country (BT-40) is not an ISO 3166 code",
        xpath=f"{_SELLER}/cac:PostalAddress/cac:Country/cbc:IdentificationCode",
        change=Change.SET_TEXT,
        value="ZZ",
    ),
    Mutation(
        rule_id="BR-CL-17",
        syntax=Syntax.UBL,
        breaks="VAT category code (BT-118) is not one the standard defines",
        xpath="/*/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID",
        change=Change.SET_TEXT,
        value="Q",
        collateral=frozenset({"BR-S-01"}),
    ),
    Mutation(
        rule_id="BR-CO-09",
        syntax=Syntax.UBL,
        breaks="Seller VAT identifier (BT-31) has no country prefix",
        xpath=f"{_SELLER}/cac:PartyTaxScheme/cbc:CompanyID",
        change=Change.SET_TEXT,
        value="123456789",
    ),
    Mutation(
        rule_id="BR-CL-01",
        syntax=Syntax.CII,
        breaks="Invoice type code is not one the standard allows",
        xpath="/*/rsm:ExchangedDocument/ram:TypeCode",
        change=Change.SET_TEXT,
        value="999",
    ),
    Mutation(
        rule_id="BR-CL-14",
        syntax=Syntax.CII,
        breaks="Seller country is not an ISO 3166 code",
        xpath=f"{_CII_SELLER}/ram:PostalTradeAddress/ram:CountryID",
        change=Change.SET_TEXT,
        value="ZZ",
    ),
    Mutation(
        rule_id="BR-CO-09",
        syntax=Syntax.CII,
        breaks="Seller VAT identifier has no country prefix",
        xpath=f"{_CII_SELLER}/ram:SpecifiedTaxRegistration/ram:ID",
        change=Change.SET_TEXT,
        value="123456789",
    ),
)

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
    """The mutation did not apply — its selector matched nothing."""


@dataclass(frozen=True)
class Mutation:
    """One deliberate breakage of a valid invoice."""

    rule_id: str
    syntax: Syntax
    breaks: str
    xpath: str
    #: Rules that this breakage unavoidably trips as well as `rule_id`.
    collateral: frozenset[str] = field(default_factory=frozenset)

    @property
    def expected(self) -> frozenset[str]:
        return self.collateral | {self.rule_id}

    @property
    def name(self) -> str:
        return f"{self.rule_id}-{self.syntax}"

    def apply(self, invoice: Path) -> bytes:
        """Return the document with the targeted nodes removed.

        Raises when the selector matches nothing. That check is the point: a
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
        for node in targets:
            parent = node.getparent()
            if parent is None:
                raise MutationError(f"{self.name}: refusing to remove the root element")
            parent.remove(node)
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")


_SELLER = "/*/cac:AccountingSupplierParty/cac:Party"
_BUYER = "/*/cac:AccountingCustomerParty/cac:Party"
_CII_AGREEMENT = "/*/rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement"
_CII_SELLER = f"{_CII_AGREEMENT}/ram:SellerTradeParty"
_CII_BUYER = f"{_CII_AGREEMENT}/ram:BuyerTradeParty"

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
        breaks="Invoice number (BT-1) removed",
        xpath="/*/cbc:ID",
    ),
    Mutation(
        rule_id="BR-03",
        syntax=Syntax.UBL,
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
        breaks="Invoice number (BT-1) removed",
        xpath="/*/rsm:ExchangedDocument/ram:ID",
    ),
)
